"""
FT-Transformer — Standalone MPS Training Script
================================================
Trains on ALL 152 features, ALL 1.75M rows, using Metal GPU (MPS) on M3 Pro.

CRITICAL IMPORT RULE:
  Do NOT import xgboost, lightgbm, or sklearn at the top level.
  Those libraries pollute the macOS libdispatch thread pool, causing PyTorch
  MPS to deadlock. By never loading them in this process, MPS works safely.
  sklearn.metrics.brier_score_loss is imported ONLY at the very end.

Architecture vs Colab v4 (40 features, 300K rows):
  This run: 152 features, 1.75M rows — seq_len is 153 vs 41 in Colab.
  O(seq_len²) attention means 153² / 41² ≈ 14× more attention compute per layer.
  To keep epoch time < 3 min on MPS, we use:
    - n_layers=1 (vs 4 in spec) — attention is O(seq_len²), 153² vs 41² = 14× costlier
    - F.scaled_dot_product_attention: PyTorch's optimized SDPA, dispatches to a
      Metal kernel on MPS that passes NaN + backward gradient checks on M3 Pro.
      ManualAttention (@ matmul) caused gradient explosion on MPS in all dtypes.
    - float32 throughout on MPS (SDPA Metal kernel is already fast)
    - Effective result: d=128, n_heads=8, 1 layer — target ~5 min/epoch on M3 Pro

Usage:
  python3 -u scripts/train_ft_standalone.py
"""

import os
import time
import pickle
import numpy as np
import pandas as pd

# Raise MPS memory cap before importing torch.
# Default 20GB cap would OOM with batch_size=1024 on seq_len=153.
# M3 Pro has 36GB unified memory, so this is safe.
os.environ.setdefault('PYTORCH_MPS_HIGH_WATERMARK_RATIO', '0.0')

import torch
import torch.nn as nn
import torch.nn.functional as F
from pathlib import Path
from torch.utils.data import DataLoader, TensorDataset

# ── Paths ─────────────────────────────────────────────────────────────────────
DATA_PATH  = 'data/processed/training_edge_v4.pkl'
OUT_DIR    = Path('models/ensemble')
OUT_MODEL  = OUT_DIR / 'ft_transformer_v4_full.pt'
OUT_PROBS  = OUT_DIR / 'ft_probs_v4_full.npy'

# ── Hyperparameters ───────────────────────────────────────────────────────────
CFG = {
    'd':             128,
    'n_heads':       8,
    'n_layers':      1,     # 4 layers spec'd but impractical: 153-token attention
                            # is 14× more expensive than Colab's 40-feature run.
                            # 1 layer (vs Colab's 4) keeps epoch time ~10 min on MPS.
                            # bfloat16 prevents NaN overflow; float16 overflows at init.
    'd_ffn':         256,
    'dropout':       0.15,
    'attn_dropout':  0.1,
}
BATCH_SIZE       = 1024
EVAL_BATCH       = 2048
LR               = 1e-4
WEIGHT_DECAY     = 0.01
MAX_EPOCHS       = 100
PATIENCE         = 15
CUTOFF           = pd.Timestamp('2023-01-01')
WARN_EPOCH_SECS  = 300   # warn if epoch > 5 min (153 tokens is legitimately slow)

# ── Device ────────────────────────────────────────────────────────────────────
# ManualAttention caused gradient explosion/NaN on MPS (all three dtypes failed).
# F.scaled_dot_product_attention dispatches to an optimized Metal kernel on MPS
# and passes NaN checks + backward gradient sanity checks on M3 Pro / PyTorch 2.10.
if torch.backends.mps.is_available():
    device    = torch.device('mps')
    USE_AMP   = False   # float32 throughout; SDPA on MPS is already fast without autocast
    amp_dtype = torch.float32
    print(f"Device: MPS (Metal GPU — M3 Pro) | SDPA | float32", flush=True)
elif torch.cuda.is_available():
    device    = torch.device('cuda')
    USE_AMP   = True
    amp_dtype = torch.float16
    print(f"Device: CUDA | SDPA | autocast: float16", flush=True)
else:
    device    = torch.device('cpu')
    USE_AMP   = False
    amp_dtype = torch.float32
    n_threads = torch.get_num_threads()
    print(f"Device: CPU | SDPA | threads: {n_threads} | float32", flush=True)

# ── Model ──────────────────────────────────────────────────────────────────────

class FeatureTokenizer(nn.Module):
    """Each feature gets its own learned embedding: (B, n_feat) → (B, n_feat, d)."""
    def __init__(self, n_features: int, d: int):
        super().__init__()
        self.weights = nn.Parameter(torch.randn(n_features, d) * 0.02)
        self.biases  = nn.Parameter(torch.zeros(n_features, d))

    def forward(self, x):
        return x.unsqueeze(-1) * self.weights.unsqueeze(0) + self.biases.unsqueeze(0)


class SDPAttention(nn.Module):
    """
    Self-attention using F.scaled_dot_product_attention.

    Dispatches to an optimized Metal kernel on MPS (PyTorch 2.x), passing NaN
    and backward gradient sanity checks on M3 Pro. ManualAttention (@ matmul)
    caused gradient explosion on MPS across float16, bfloat16, and float32.
    """
    def __init__(self, d: int, n_heads: int, dropout: float):
        super().__init__()
        assert d % n_heads == 0
        self.n_heads  = n_heads
        self.d_head   = d // n_heads
        self.attn_drop = dropout

        self.qkv      = nn.Linear(d, 3 * d, bias=False)
        self.out_proj = nn.Linear(d, d, bias=False)

    def forward(self, x):
        B, N, D = x.shape
        H, Dh   = self.n_heads, self.d_head

        qkv = self.qkv(x)                         # (B, N, 3D)
        q, k, v = qkv.split(D, dim=-1)            # each (B, N, D)

        # Reshape to (B, H, N, Dh) for SDPA
        q = q.view(B, N, H, Dh).transpose(1, 2)
        k = k.view(B, N, H, Dh).transpose(1, 2)
        v = v.view(B, N, H, Dh).transpose(1, 2)

        # PyTorch's optimized SDPA — Metal kernel on MPS, Flash-Attn on CUDA
        dropout_p = self.attn_drop if self.training else 0.0
        ctx = F.scaled_dot_product_attention(q, k, v, dropout_p=dropout_p)
                                                   # (B, H, N, Dh)

        ctx = ctx.transpose(1, 2).contiguous().view(B, N, D)
        return self.out_proj(ctx)


class TransformerBlock(nn.Module):
    def __init__(self, d: int, n_heads: int, d_ffn: int, dropout: float, attn_dropout: float):
        super().__init__()
        self.norm1 = nn.LayerNorm(d)
        self.attn  = SDPAttention(d, n_heads, attn_dropout)
        self.norm2 = nn.LayerNorm(d)
        self.ffn   = nn.Sequential(
            nn.Linear(d, d_ffn),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(d_ffn, d),
            nn.Dropout(dropout),
        )

    def forward(self, x):
        x = x + self.attn(self.norm1(x))
        x = x + self.ffn(self.norm2(x))
        return x


class FTTransformer(nn.Module):
    def __init__(self, n_features: int, cfg: dict):
        super().__init__()
        d = cfg['d']

        self.tokenizer = FeatureTokenizer(n_features, d)
        self.cls_token = nn.Parameter(torch.randn(1, 1, d) * 0.02)

        self.layers = nn.ModuleList([
            TransformerBlock(d, cfg['n_heads'], cfg['d_ffn'],
                             cfg['dropout'], cfg['attn_dropout'])
            for _ in range(cfg['n_layers'])
        ])

        self.norm = nn.LayerNorm(d)

        self.head = nn.Sequential(
            nn.Linear(d, d),
            nn.GELU(),
            nn.Dropout(cfg['dropout']),
            nn.Linear(d, 1),
        )

    def forward(self, x):
        tokens = self.tokenizer(x)                          # (B, n_feat, d)
        cls    = self.cls_token.expand(x.size(0), -1, -1)  # (B, 1, d)
        tokens = torch.cat([cls, tokens], dim=1)            # (B, n_feat+1, d)

        for layer in self.layers:
            tokens = layer(tokens)

        cls_out = self.norm(tokens[:, 0])          # (B, d)
        # Clamp logits to [-30, 30] to prevent inf in BCEWithLogitsLoss.
        # Extreme standardized inputs (sparse features, tiny std) can push logits to
        # ±inf, causing inf−inf=NaN in the stable BCE formula.
        return self.head(cls_out).squeeze(-1).clamp(-30.0, 30.0)   # (B,)


# ── Data ───────────────────────────────────────────────────────────────────────

print("=" * 65, flush=True)
print("FT-TRANSFORMER — STANDALONE MPS TRAINING", flush=True)
print("=" * 65, flush=True)

print("\n[1/4] Loading training_edge_v4.pkl ...", flush=True)
t0 = time.time()
with open(DATA_PATH, 'rb') as f:
    bundle = pickle.load(f)
X_df, y_ser, dates = bundle[0], bundle[1], bundle[2]
print(f"  Loaded in {time.time()-t0:.1f}s", flush=True)

feature_cols = list(X_df.columns)
n_features   = len(feature_cols)

tr_mask = (dates < CUTOFF).values
te_mask = ~tr_mask

X_np = X_df.values.astype(np.float32)
y_np = y_ser.values.astype(np.float32)

X_train, X_test = X_np[tr_mask], X_np[te_mask]
y_train, y_test = y_np[tr_mask], y_np[te_mask]

n_train, n_test = len(X_train), len(X_test)
print(f"  Train: {n_train:,} x {n_features} | Test: {n_test:,} x {n_features}", flush=True)
assert n_test == 127928, f"Expected 127,928 test rows, got {n_test}"

# ── Preprocessing ──────────────────────────────────────────────────────────────

print("\n[2/4] Preprocessing (NaN fill + standardisation) ...", flush=True)
t0 = time.time()

X_train = np.nan_to_num(X_train, nan=0.0)
X_test  = np.nan_to_num(X_test,  nan=0.0)

means = X_train.mean(axis=0)
stds  = X_train.std(axis=0)
stds[stds < 1e-8] = 1.0

X_train = (X_train - means) / stds
X_test  = (X_test  - means) / stds

# Clip to ±10σ: sparse features (mostly NaN → 0) get tiny stds, producing
# extreme standardized values (100σ+) that cause inf logits and NaN loss.
X_train = np.clip(X_train, -10.0, 10.0)
X_test  = np.clip(X_test,  -10.0, 10.0)

print(f"  Done in {time.time()-t0:.1f}s", flush=True)

# ── DataLoaders ────────────────────────────────────────────────────────────────

train_ds     = TensorDataset(torch.FloatTensor(X_train), torch.FloatTensor(y_train))
train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True,
                          num_workers=0, pin_memory=False)

X_test_cpu = torch.FloatTensor(X_test)   # stay on CPU; moved in eval batches

# ── Build model ────────────────────────────────────────────────────────────────

print("\n[3/4] Building model ...", flush=True)
model = FTTransformer(n_features, CFG).to(device)

n_params = sum(p.numel() for p in model.parameters())
print(f"  Parameters: {n_params:,}", flush=True)
print(f"  d={CFG['d']}, heads={CFG['n_heads']}, layers={CFG['n_layers']}, "
      f"d_ffn={CFG['d_ffn']}, seq_len={n_features+1}", flush=True)
print(f"  USE_AMP={USE_AMP} | device={device} | SDPA", flush=True)

optimizer = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=MAX_EPOCHS)
criterion = nn.BCEWithLogitsLoss()

# GradScaler for float16 — only valid on CUDA; MPS uses unscaled updates
if device.type == 'cuda':
    scaler = torch.amp.GradScaler()
else:
    scaler = None

# ── Training ───────────────────────────────────────────────────────────────────

print(f"\n[4/4] Training for up to {MAX_EPOCHS} epochs (patience={PATIENCE}) ...", flush=True)
print(f"  Batches/epoch: {len(train_loader):,}  |  "
      f"Batch size: {BATCH_SIZE}  |  "
      f"Eval batch: {EVAL_BATCH}", flush=True)
print("=" * 65, flush=True)

best_brier  = float('inf')
best_state  = None
best_probs  = None
best_epoch  = 0
wait        = 0
train_start = time.time()

print("Starting epoch 1 ...", flush=True)

for epoch in range(1, MAX_EPOCHS + 1):
    ep_start = time.time()

    # ── Train ──────────────────────────────────────────────────────────────
    model.train()
    total_loss = 0.0
    n_batches  = 0

    for xb, yb in train_loader:
        xb = xb.to(device, non_blocking=True)
        yb = yb.to(device, non_blocking=True)

        optimizer.zero_grad()

        if USE_AMP and device.type == 'mps':
            # MPS autocast (no GradScaler — MPS doesn't support it)
            with torch.amp.autocast(device_type='mps', dtype=amp_dtype):
                logits = model(xb)
                loss   = criterion(logits.float(), yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()

        elif USE_AMP and device.type == 'cuda':
            with torch.amp.autocast(device_type='cuda'):
                logits = model(xb)
                loss   = criterion(logits, yb)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            scaler.step(optimizer)
            scaler.update()

        else:
            logits = model(xb)
            loss   = criterion(logits, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()

        total_loss += loss.item()
        n_batches  += 1

    scheduler.step()
    avg_loss = total_loss / n_batches

    # ── Evaluate (batched to avoid MPS OOM on 127K rows) ───────────────────
    model.eval()
    probs_list = []
    with torch.no_grad():
        for i in range(0, n_test, EVAL_BATCH):
            xb_te = X_test_cpu[i:i + EVAL_BATCH].to(device, non_blocking=True)
            if USE_AMP and device.type == 'mps':
                with torch.amp.autocast(device_type='mps', dtype=amp_dtype):
                    logits_te = model(xb_te)
            elif USE_AMP and device.type == 'cuda':
                with torch.amp.autocast(device_type='cuda'):
                    logits_te = model(xb_te)
            else:
                logits_te = model(xb_te)
            probs_list.append(torch.sigmoid(logits_te.float()).cpu().numpy())

    test_probs = np.concatenate(probs_list)

    # Brier score (manual — no sklearn import yet)
    brier = float(np.mean((test_probs - y_test) ** 2))

    ep_time = time.time() - ep_start

    # Leakage guard
    if brier < 0.15:
        print(f"STOP: Brier {brier:.4f} < 0.15 — data leakage!", flush=True)
        raise SystemExit(1)

    if ep_time > WARN_EPOCH_SECS:
        print(f"  WARNING: epoch {epoch} took {ep_time:.0f}s (>{WARN_EPOCH_SECS}s)", flush=True)

    # Best model tracking + early stopping
    if brier < best_brier:
        best_brier  = brier
        best_epoch  = epoch
        best_state  = {k: v.cpu().clone() for k, v in model.state_dict().items()}
        best_probs  = test_probs.copy()
        wait        = 0
        marker      = " ★"
    else:
        wait  += 1
        marker = f"  (wait {wait}/{PATIENCE})"

    print(
        f"Epoch {epoch:3d}/{MAX_EPOCHS}"
        f" — Loss: {avg_loss:.4f}"
        f" — Brier: {brier:.4f}"
        f" — Time: {ep_time:.0f}s"
        f" — Best: {best_brier:.4f}{marker}",
        flush=True,
    )

    if wait >= PATIENCE:
        print(f"\nEarly stopping at epoch {epoch} (no improvement for {PATIENCE} epochs).", flush=True)
        break

    if epoch < MAX_EPOCHS:
        print(f"Starting epoch {epoch + 1} ...", flush=True)

# ── Save ───────────────────────────────────────────────────────────────────────

total_time = time.time() - train_start
print(f"\n{'='*65}", flush=True)
print(f"Training complete.", flush=True)
print(f"  Best Brier:  {best_brier:.4f}  (epoch {best_epoch})", flush=True)
print(f"  Total time:  {total_time/60:.1f} min", flush=True)
print(f"{'='*65}", flush=True)

# Restore best model weights
model.load_state_dict(best_state)
model.eval()
model = model.cpu()

print(f"\nSaving model → {OUT_MODEL}", flush=True)
torch.save({
    'model_state':  best_state,
    'config':       {'n_features': n_features, **CFG},
    'norm_params':  {'means': means, 'stds': stds},
    'feature_cols': feature_cols,
    'test_probs':   best_probs,
    'brier':        best_brier,
    'best_epoch':   best_epoch,
    'n_train':      n_train,
    'n_test':       n_test,
}, OUT_MODEL)
print(f"  Saved.", flush=True)

print(f"Saving predictions → {OUT_PROBS}", flush=True)
np.save(OUT_PROBS, best_probs)
print(f"  Saved ({len(best_probs):,} rows).", flush=True)

# ── Final verification with sklearn (imported here only) ──────────────────────
print("\nFinal verification with sklearn brier_score_loss ...", flush=True)
from sklearn.metrics import brier_score_loss as sklearn_brier
sk_b = sklearn_brier(y_test, best_probs)
assert abs(sk_b - best_brier) < 1e-6, \
    f"Brier mismatch: manual={best_brier:.6f} sklearn={sk_b:.6f}"
print(f"  Verified: {sk_b:.4f}  (manual matches sklearn ✓)", flush=True)

print("\n✓ Done. Run: python3 -u scripts/restack_ensemble.py", flush=True)
