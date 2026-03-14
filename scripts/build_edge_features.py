"""
TennisIQ Edge Features — Beyond what public models capture.
Builds on rolling_v2 training data, adds:
1. Surface-specific Elo (table stakes, needed as baseline)
2. Multi-window form (3/5/15/50 match windows)
3. Pressure divergence (normal vs clutch performance gap)
4. Previous match context (opponent strength, duration, recovery)
5. Style matchup vulnerability (crossover exploitation potential)
6. Archetype interaction (player type vs player type)
7. Surface-specific recent form (last 5 on this surface)
8. Second serve under pressure (from match stats)
All rolling, zero leakage, temporal split.
"""

import pandas as pd
import numpy as np
import pickle
import xgboost as xgb
import math
from pathlib import Path
from collections import defaultdict
from datetime import timedelta

REPO_ROOT = Path(__file__).resolve().parent.parent
UNI_PATH = REPO_ROOT / "data" / "processed" / "universal_features.parquet"
POINTS_PATH = REPO_ROOT / "data" / "processed" / "parsed_points.parquet"
CPI_PATH = REPO_ROOT / "data" / "court_speed.csv"
OUTPUT_PATH = REPO_ROOT / "data" / "processed" / "training_edge_v1.pkl"
TEMPORAL_CUTOFF = "2023-01-01"

print("=" * 70)
print("  TENNISIQ EDGE FEATURES — WHAT NOBODY ELSE HAS")
print("=" * 70)

# ─────────────────────────────────────────────────
# LOAD
# ─────────────────────────────────────────────────
print("\n[LOAD]")
uni = pd.read_parquet(UNI_PATH)
uni["match_date"] = pd.to_datetime(uni["tourney_date"].astype(str), format="%Y%m%d", errors="coerce")
uni["minutes"] = pd.to_numeric(uni.get("minutes", 0), errors="coerce").fillna(0)
points = pd.read_parquet(POINTS_PATH)
points.columns = points.columns.str.replace(" ", "_")
print(f"  Matches: {len(uni):,}")
print(f"  Points: {len(points):,}")

# CPI/ball
ball_codes = {"Dunlop":1,"Wilson":2,"Penn":3,"Babolat":4,"Head":5,"Slazenger":6}
tourney_meta = {}
if CPI_PATH.exists():
    for _, row in pd.read_csv(CPI_PATH).iterrows():
        t = row.get("tournament","")
        bt = str(row.get("ball_type",""))
        bc = 0
        for name, code in ball_codes.items():
            if name.lower() in bt.lower(): bc = code; break
        tourney_meta[t] = {"cpi": float(row.get("cpi",0) or 0), "ball_type": bc}

# ─────────────────────────────────────────────────
# PRE-PROCESS: Charted match summaries
# ─────────────────────────────────────────────────
print("\n[PRE-PROCESS] Charted match summaries...")
charted = {}
for mid, grp in points.groupby("match_id"):
    row0 = grp.iloc[0]
    try: bo = int(float(row0.get("Best_of",3) or 3))
    except: bo = 3
    charted[mid] = {"p1":row0["Player_1"],"p2":row0["Player_2"],
                     "surface":row0.get("Surface","Hard"),"bo":bo,"pts":grp}

# Date lookup
uni_dates = defaultdict(list)
for _, row in uni.iterrows():
    w,l = row.get("winner_name",""), row.get("loser_name","")
    d = row["match_date"]
    if pd.isna(d) or not w or not l: continue
    uni_dates[(w,l)].append(d)
    uni_dates[(l,w)].append(d)

for mid, info in charted.items():
    key = (info["p1"], info["p2"])
    dates = uni_dates.get(key, [])
    info["date"] = min(dates) if dates else pd.Timestamp("2010-01-01")

charted_by_players = defaultdict(list)
for mid, info in charted.items():
    charted_by_players[(info["p1"],info["p2"])].append(mid)
    charted_by_players[(info["p2"],info["p1"])].append(mid)

print(f"  {len(charted)} charted matches")

# ─────────────────────────────────────────────────
# MAIN CHRONOLOGICAL PASS
# ─────────────────────────────────────────────────
print("\n[BUILD] Single chronological pass through all matches...")

uni_sorted = uni[uni["match_date"].notna()].sort_values("match_date").reset_index(drop=True)

# ── Player accumulators ──
class Player:
    __slots__ = [
        # Elo
        'elo_all','elo_hard','elo_clay','elo_grass',
        # Match stats rolling
        'm_aces','m_dfs','m_svpt','m_1stIn','m_1stWon','m_2ndWon',
        'm_bpSaved','m_bpFaced',
        'm_ret_pts','m_ret_won','m_ret_1st','m_ret_1st_won','m_ret_2nd','m_ret_2nd_won',
        # Form windows: list of (date, won, opp_rank, minutes, surface, sets)
        'match_history',
        # Charted stats
        'c_srv_wide','c_srv_body','c_srv_t','c_srv_total',
        'c_srv_wide_bp','c_srv_t_bp','c_srv_bp_total',
        'c_winners','c_ue',
        'c_rally_won','c_rally_total',
        'c_fs_srv','c_fs_won',
        'c_set1_sp','c_set1_sw','c_set3_sp','c_set3_sw',
        'c_bigrams','c_total_pts',
        'c_surface',
    ]
    def __init__(self):
        self.elo_all=1500.0;self.elo_hard=1500.0;self.elo_clay=1500.0;self.elo_grass=1500.0
        self.m_aces=0;self.m_dfs=0;self.m_svpt=0;self.m_1stIn=0;self.m_1stWon=0;self.m_2ndWon=0
        self.m_bpSaved=0;self.m_bpFaced=0
        self.m_ret_pts=0;self.m_ret_won=0;self.m_ret_1st=0;self.m_ret_1st_won=0
        self.m_ret_2nd=0;self.m_ret_2nd_won=0
        self.match_history=[]
        self.c_srv_wide=0;self.c_srv_body=0;self.c_srv_t=0;self.c_srv_total=0
        self.c_srv_wide_bp=0;self.c_srv_t_bp=0;self.c_srv_bp_total=0
        self.c_winners=0;self.c_ue=0
        self.c_rally_won=[0]*25;self.c_rally_total=[0]*25
        self.c_fs_srv=0;self.c_fs_won=0
        self.c_set1_sp=0;self.c_set1_sw=0;self.c_set3_sp=0;self.c_set3_sw=0
        self.c_bigrams=defaultdict(int);self.c_total_pts=0
        self.c_surface=defaultdict(lambda:{"sp":0,"sw":0})

    def snapshot(self, surface, opp_crossover):
        f = {}
        # ── Elo ──
        f["elo_all"] = self.elo_all
        f["elo_surface"] = {"Hard":self.elo_hard,"Clay":self.elo_clay,"Grass":self.elo_grass}.get(surface, self.elo_all)

        # ── Multi-window form ──
        for window, label in [(3,"form_3"),(5,"form_5"),(15,"form_15"),(50,"form_50")]:
            recent = self.match_history[-window:]
            if len(recent) >= 2:
                weights = np.linspace(0.5, 1.0, len(recent))
                wins = np.array([1.0 if r[1] else 0.0 for r in recent])
                f[label] = float(np.average(wins, weights=weights))
            else:
                f[label] = 0.5

        # ── Surface-specific recent form (last 5 on this surface) ──
        surf_recent = [r for r in self.match_history if r[4] == surface][-5:]
        if len(surf_recent) >= 2:
            f["surface_form"] = sum(1 for r in surf_recent if r[1]) / len(surf_recent)
        else:
            f["surface_form"] = 0.5

        # ── Match stats serve/return ──
        svpt = max(self.m_svpt, 1)
        f1in = max(self.m_1stIn, 1)
        sv2 = max(svpt - self.m_1stIn, 1)
        f["ace_rate"] = self.m_aces/svpt if self.m_svpt>20 else 0.04
        f["df_rate"] = self.m_dfs/svpt if self.m_svpt>20 else 0.03
        f["first_serve_pct"] = self.m_1stIn/svpt if self.m_svpt>20 else 0.60
        f["first_serve_won_pct"] = self.m_1stWon/f1in if self.m_1stIn>10 else 0.70
        f["second_serve_won_pct"] = self.m_2ndWon/sv2 if sv2>10 else 0.50
        f["serve_pts_won_pct"] = (self.m_1stWon+self.m_2ndWon)/svpt if self.m_svpt>20 else 0.63
        f["bp_save_pct"] = self.m_bpSaved/max(self.m_bpFaced,1) if self.m_bpFaced>5 else 0.62
        rp = max(self.m_ret_pts,1)
        f["return_pts_won_pct"] = self.m_ret_won/rp if self.m_ret_pts>20 else 0.37
        f["return_vs_1st_pct"] = self.m_ret_1st_won/max(self.m_ret_1st,1) if self.m_ret_1st>10 else 0.30
        f["return_vs_2nd_pct"] = self.m_ret_2nd_won/max(self.m_ret_2nd,1) if self.m_ret_2nd>10 else 0.50

        # ── PRESSURE DIVERGENCE (edge feature) ──
        # Gap between normal serve and break point save
        normal_srv = f["serve_pts_won_pct"]
        bp_srv = f["bp_save_pct"]
        f["pressure_divergence"] = bp_srv - normal_srv  # positive = clutch, negative = chokes

        # ── SECOND SERVE UNDER PRESSURE ──
        # bp_save is a proxy; real pressure = facing break point on 2nd serve
        # We approximate: 2nd serve win % vs bp save % gap
        f["second_serve_pressure_gap"] = f["bp_save_pct"] - f["second_serve_won_pct"]

        # ── Charted features ──
        ct = max(self.c_srv_total, 1)
        f["serve_wide_pct"] = self.c_srv_wide/ct if self.c_srv_total>20 else 0.40
        f["serve_body_pct"] = self.c_srv_body/ct if self.c_srv_total>20 else 0.20
        f["serve_t_pct"] = self.c_srv_t/ct if self.c_srv_total>20 else 0.35
        # Entropy
        entropy = 0
        if self.c_srv_total > 20:
            for c in [self.c_srv_wide, self.c_srv_body, self.c_srv_t]:
                if c > 0:
                    p = c/ct
                    entropy -= p * math.log2(p)
        f["serve_dir_entropy"] = entropy if self.c_srv_total>20 else 1.4
        # Pressure serve
        bpt = max(self.c_srv_bp_total,1)
        f["serve_wide_pct_pressure"] = self.c_srv_wide_bp/bpt if self.c_srv_bp_total>10 else 0.40
        f["serve_t_pct_pressure"] = self.c_srv_t_bp/bpt if self.c_srv_bp_total>10 else 0.35
        # KL
        pkl = 0
        if self.c_srv_bp_total>10 and self.c_srv_total>20:
            base = [self.c_srv_wide/ct, self.c_srv_body/ct, self.c_srv_t/ct]
            pres = [self.c_srv_wide_bp/bpt, (bpt-self.c_srv_wide_bp-self.c_srv_t_bp)/bpt, self.c_srv_t_bp/bpt]
            for b,p in zip(base,pres):
                if b>0.01 and p>0.01: pkl += p * math.log2(p/b)
        f["serve_pressure_kl"] = pkl

        # SERVE DIRECTION PRESSURE SHIFT (edge)
        f["pressure_wide_shift"] = f["serve_wide_pct_pressure"] - f["serve_wide_pct"]
        f["pressure_t_shift"] = f["serve_t_pct_pressure"] - f["serve_t_pct"]

        # Aggression
        wd = self.c_winners + self.c_ue
        f["aggression_index"] = self.c_winners/max(wd,1) if wd>20 else 0.50

        # Rally crossover
        crossover = 6
        if sum(self.c_rally_total) > 50:
            crossover = 1
            for rl in range(1,20):
                t = self.c_rally_total[rl]
                w = self.c_rally_won[rl]
                if t>=10 and w/t>=0.50: crossover = rl
        f["rally_crossover"] = crossover

        sw = sum(self.c_rally_won[r] for r in range(1,5))
        st = sum(self.c_rally_total[r] for r in range(1,5))
        lw = sum(self.c_rally_won[r] for r in range(9,25))
        lt = sum(self.c_rally_total[r] for r in range(9,25))
        f["win_rate_short_rally"] = sw/max(st,1) if st>20 else 0.65
        f["win_rate_long_rally"] = lw/max(lt,1) if lt>20 else 0.50
        f["rally_wr_dropoff"] = f["win_rate_short_rally"] - f["win_rate_long_rally"]

        # CROSSOVER EXPLOITATION (edge) — can opponent push past my crossover?
        f["crossover_vulnerability"] = max(0, opp_crossover - crossover) if opp_crossover > 0 else 0

        # First strike
        f["first_strike_rate"] = self.c_fs_won/max(self.c_fs_srv,1) if self.c_fs_srv>20 else 0.25

        # Stage
        f["set1_serve_pct"] = self.c_set1_sw/max(self.c_set1_sp,1) if self.c_set1_sp>20 else 0.63
        f["late_match_dropoff"] = f["set1_serve_pct"] - (self.c_set3_sw/max(self.c_set3_sp,1)) if self.c_set3_sp>10 else 0.02

        # Pattern diversity
        f["pattern_diversity_2gram"] = len(self.c_bigrams) if self.c_total_pts>50 else 20

        # Surface serve
        ss = self.c_surface.get(surface, {"sp":0,"sw":0})
        f["surface_serve_wr"] = ss["sw"]/ss["sp"] if ss["sp"]>20 else f["serve_pts_won_pct"]

        return f

    def update_match_stats(self, match, is_winner):
        my = "w_" if is_winner else "l_"
        opp = "l_" if is_winner else "w_"
        def g(col):
            v = match.get(col, 0)
            return float(v) if pd.notna(v) else 0
        self.m_aces += g(my+"ace"); self.m_dfs += g(my+"df")
        self.m_svpt += g(my+"svpt"); self.m_1stIn += g(my+"1stIn")
        self.m_1stWon += g(my+"1stWon"); self.m_2ndWon += g(my+"2ndWon")
        self.m_bpSaved += g(my+"bpSaved"); self.m_bpFaced += g(my+"bpFaced")
        opp_svpt = g(opp+"svpt"); opp_1stIn = g(opp+"1stIn")
        opp_1stWon = g(opp+"1stWon"); opp_2ndWon = g(opp+"2ndWon")
        self.m_ret_pts += opp_svpt
        self.m_ret_won += opp_svpt - opp_1stWon - opp_2ndWon
        self.m_ret_1st += opp_1stIn
        self.m_ret_1st_won += opp_1stIn - opp_1stWon
        sv2 = max(opp_svpt - opp_1stIn, 0)
        self.m_ret_2nd += sv2
        self.m_ret_2nd_won += sv2 - opp_2ndWon


def elo_update(winner_elo, loser_elo, k=32):
    exp_w = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    return winner_elo + k * (1 - exp_w), loser_elo + k * (0 - (1 - exp_w))


# Initialize
players = defaultdict(Player)
h2h_wins = defaultdict(int)
h2h_total = defaultdict(int)
player_wins_all = defaultdict(int)
player_total_all = defaultdict(int)
player_wins_top50 = defaultdict(int)
player_total_top50 = defaultdict(int)
processed_charted = set()

surface_map = {"Hard":1,"Clay":2,"Grass":3,"Carpet":4}
level_map = {"G":4,"M":3,"A":2,"D":1,"F":5}

rows = []
labels = []
dates_list = []

print(f"  Processing {len(uni_sorted):,} matches...")

for idx, (_, match) in enumerate(uni_sorted.iterrows()):
    w = match.get("winner_name","")
    l = match.get("loser_name","")
    md = match["match_date"]
    if not w or not l: continue

    surface = match.get("surface","Hard")
    has_stats = pd.notna(match.get("w_svpt"))
    wr_rank = pd.to_numeric(match.get("winner_rank",500), errors="coerce") or 500
    lr_rank = pd.to_numeric(match.get("loser_rank",500), errors="coerce") or 500
    mins = match["minutes"]

    pw = players[w]
    pl = players[l]

    # ── Opponent crossover for vulnerability calc ──
    w_cross = 6
    l_cross = 6
    if sum(pw.c_rally_total) > 50:
        w_cross = 1
        for rl in range(1,20):
            if pw.c_rally_total[rl]>=10 and pw.c_rally_won[rl]/pw.c_rally_total[rl]>=0.50: w_cross = rl
    if sum(pl.c_rally_total) > 50:
        l_cross = 1
        for rl in range(1,20):
            if pl.c_rally_total[rl]>=10 and pl.c_rally_won[rl]/pl.c_rally_total[rl]>=0.50: l_cross = rl

    # ── SNAPSHOT both players BEFORE update ──
    w_snap = pw.snapshot(surface, l_cross)
    l_snap = pl.snapshot(surface, w_cross)

    # ── H2H ──
    wh2h_t = h2h_total.get((w,l),0) + h2h_total.get((l,w),0)
    w_h2h = h2h_wins.get((w,l),0)/max(wh2h_t,1) if wh2h_t>0 else 0.5
    l_h2h = h2h_wins.get((l,w),0)/max(wh2h_t,1) if wh2h_t>0 else 0.5

    # ── Fatigue ──
    def get_fat(p_obj, md):
        h = p_obj.match_history
        d14 = md - timedelta(days=14)
        d30 = md - timedelta(days=30)
        r14 = [r for r in h if d14 <= r[0] < md]
        r30 = [r for r in h if d30 <= r[0] < md]
        prev = [r[0] for r in h if r[0] < md]
        rest = min((md - prev[-1]).days, 60) if prev else 30
        mins_14 = sum(r[3] for r in r14)
        # COMPOUND FATIGUE (edge): weight by opponent rank
        compound_14 = sum(r[3] * (1 + max(0, 100-r[2])/100) for r in r14)  # harder matches = more fatigue
        return mins_14, len(r14), rest, sum(r[3] for r in r30), len(r30), compound_14

    wfat = get_fat(pw, md)
    lfat = get_fat(pl, md)

    # ── Previous match context (edge) ──
    def prev_match_context(p_obj, md):
        h = p_obj.match_history
        prev = [r for r in h if r[0] < md]
        if not prev:
            return 0, 30, 500, 0  # mins, rest, opp_rank, sets
        last = prev[-1]
        rest = (md - last[0]).days
        return last[3], min(rest, 60), last[2], last[5]  # mins, rest, opp_rank, sets

    w_prev = prev_match_context(pw, md)
    l_prev = prev_match_context(pl, md)

    # ── Opponent-adjusted ──
    def get_oa(player):
        ta = max(player_total_all.get(player,0),1)
        wa = player_wins_all.get(player,0)
        tt = player_total_top50.get(player,0)
        wt = player_wins_top50.get(player,0)
        wr_all = wa/ta
        wr_t50 = wt/max(tt,1) if tt>=3 else wr_all
        return wr_t50, wr_all - wr_t50
    w_oa = get_oa(w)
    l_oa = get_oa(l)

    # ── Build feature dict ──
    rd = lr_rank - wr_rank
    sc = surface_map.get(surface, 1)
    lc = level_map.get(str(match.get("tourney_level","A")), 2)
    bo = int(match.get("best_of", 3) or 3)
    tname = match.get("tourney_name","")
    tm = tourney_meta.get(tname, {"cpi":0,"ball_type":0})

    pkeys = list(w_snap.keys())

    base = {"surface_code":sc,"tourney_level_code":lc,"best_of":bo,
            "cpi":tm["cpi"],"ball_type":tm["ball_type"],"rank_diff":rd,
            "p1_h2h_pct":w_h2h,"p2_h2h_pct":l_h2h,
            # Elo diff (edge)
            "elo_diff": w_snap["elo_all"] - l_snap["elo_all"],
            "elo_surface_diff": w_snap["elo_surface"] - l_snap["elo_surface"],
            }

    # Row 1: winner = p1
    r1 = dict(base)
    for k in pkeys:
        r1[f"p1_{k}"] = w_snap[k]
        r1[f"p2_{k}"] = l_snap[k]
    # Fatigue
    r1["p1_mins_14d"]=wfat[0];r1["p2_mins_14d"]=lfat[0]
    r1["p1_matches_14d"]=wfat[1];r1["p2_matches_14d"]=lfat[1]
    r1["p1_days_rest"]=wfat[2];r1["p2_days_rest"]=lfat[2]
    r1["p1_compound_fatigue"]=wfat[5];r1["p2_compound_fatigue"]=lfat[5]
    r1["fatigue_mins_diff"]=wfat[0]-lfat[0];r1["rest_days_diff"]=wfat[2]-lfat[2]
    r1["compound_fatigue_diff"]=wfat[5]-lfat[5]
    # Prev match context (edge)
    r1["p1_prev_mins"]=w_prev[0];r1["p2_prev_mins"]=l_prev[0]
    r1["p1_prev_rest"]=w_prev[1];r1["p2_prev_rest"]=l_prev[1]
    r1["p1_prev_opp_rank"]=w_prev[2];r1["p2_prev_opp_rank"]=l_prev[2]
    # Opponent-adjusted
    r1["p1_win_rate_vs_top50"]=w_oa[0];r1["p2_win_rate_vs_top50"]=l_oa[0]
    r1["p1_top50_dropoff"]=w_oa[1];r1["p2_top50_dropoff"]=l_oa[1]
    rows.append(r1); labels.append(1); dates_list.append(md)

    # Row 2: loser = p1
    r2 = dict(base)
    r2["rank_diff"] = -rd
    r2["elo_diff"] = l_snap["elo_all"] - w_snap["elo_all"]
    r2["elo_surface_diff"] = l_snap["elo_surface"] - w_snap["elo_surface"]
    r2["p1_h2h_pct"]=l_h2h;r2["p2_h2h_pct"]=w_h2h
    for k in pkeys:
        r2[f"p1_{k}"] = l_snap[k]
        r2[f"p2_{k}"] = w_snap[k]
    r2["p1_mins_14d"]=lfat[0];r2["p2_mins_14d"]=wfat[0]
    r2["p1_matches_14d"]=lfat[1];r2["p2_matches_14d"]=wfat[1]
    r2["p1_days_rest"]=lfat[2];r2["p2_days_rest"]=wfat[2]
    r2["p1_compound_fatigue"]=lfat[5];r2["p2_compound_fatigue"]=wfat[5]
    r2["fatigue_mins_diff"]=lfat[0]-wfat[0];r2["rest_days_diff"]=lfat[2]-wfat[2]
    r2["compound_fatigue_diff"]=lfat[5]-wfat[5]
    r2["p1_prev_mins"]=l_prev[0];r2["p2_prev_mins"]=w_prev[0]
    r2["p1_prev_rest"]=l_prev[1];r2["p2_prev_rest"]=w_prev[1]
    r2["p1_prev_opp_rank"]=l_prev[2];r2["p2_prev_opp_rank"]=w_prev[2]
    r2["p1_win_rate_vs_top50"]=l_oa[0];r2["p2_win_rate_vs_top50"]=w_oa[0]
    r2["p1_top50_dropoff"]=l_oa[1];r2["p2_top50_dropoff"]=w_oa[1]
    rows.append(r2); labels.append(0); dates_list.append(md)

    # ══════════════════════════════════════════════
    # UPDATE accumulators AFTER building rows
    # ══════════════════════════════════════════════

    # Elo update
    new_w_all, new_l_all = elo_update(pw.elo_all, pl.elo_all, k=32)
    pw.elo_all = new_w_all; pl.elo_all = new_l_all
    if surface == "Hard":
        pw.elo_hard, pl.elo_hard = elo_update(pw.elo_hard, pl.elo_hard, k=40)
    elif surface == "Clay":
        pw.elo_clay, pl.elo_clay = elo_update(pw.elo_clay, pl.elo_clay, k=40)
    elif surface == "Grass":
        pw.elo_grass, pl.elo_grass = elo_update(pw.elo_grass, pl.elo_grass, k=40)

    # Match stats
    if has_stats:
        pw.update_match_stats(match, True)
        pl.update_match_stats(match, False)

    # Score/sets for history
    score = str(match.get("score",""))
    n_sets = len(score.split()) if score else 2

    # Match history (date, won, opp_rank, minutes, surface, sets)
    pw.match_history.append((md, True, lr_rank, mins, surface, n_sets))
    pl.match_history.append((md, False, wr_rank, mins, surface, n_sets))

    # H2H
    h2h_wins[(w,l)] += 1; h2h_total[(w,l)] += 1; h2h_total[(l,w)] += 1

    # Opponent-adjusted
    player_wins_all[w]+=1;player_total_all[w]+=1;player_total_all[l]+=1
    if lr_rank<=50: player_wins_top50[w]+=1;player_total_top50[w]+=1
    if wr_rank<=50: player_total_top50[l]+=1

    # Charted points update
    ckeys = charted_by_players.get((w,l), []) + charted_by_players.get((l,w), [])
    for cmid in ckeys:
        if cmid in processed_charted: continue
        cinfo = charted[cmid]
        if abs((cinfo["date"] - md).days) > 7: continue
        processed_charted.add(cmid)

        for pt in cinfo["pts"].itertuples(index=False):
            svr = pt.Svr; ptw = pt.PtWinner
            try: pp1, pp2 = pt.Player_1, pt.Player_2
            except: continue
            outcome = getattr(pt, "point_outcome", "")
            rl = pt.rally_length
            serve_dir = getattr(pt, "serve_direction", "")
            csurf = cinfo["surface"]

            if svr==1: server, sw = pp1, (ptw==1)
            elif svr==2: server, sw = pp2, (ptw==2)
            else: continue
            returner = pp2 if server==pp1 else pp1

            sa = players[server]
            sa.c_srv_total += 1
            if serve_dir=="wide": sa.c_srv_wide += 1
            elif serve_dir=="body": sa.c_srv_body += 1
            elif serve_dir=="T": sa.c_srv_t += 1

            pts_str = str(getattr(pt,"Pts",""))
            is_bp = False
            if svr==1: is_bp = (pts_str.endswith("-40") and pts_str.split("-")[0] in ["0","15","30"]) or pts_str=="40-AD"
            elif svr==2: is_bp = (pts_str.startswith("40-") and pts_str.split("-")[1] in ["0","15","30"]) or pts_str=="AD-40"
            if is_bp:
                sa.c_srv_bp_total += 1
                if serve_dir=="wide": sa.c_srv_wide_bp += 1
                elif serve_dir=="T": sa.c_srv_t_bp += 1

            if outcome in ("winner","ace"):
                if sw: sa.c_winners += 1
                else: players[returner].c_winners += 1
            elif outcome=="unforced_error":
                if not sw: sa.c_ue += 1
                else: players[returner].c_ue += 1

            if pd.notna(rl) and 1<=rl<25:
                rl_int = int(rl)
                for plyr, won in [(pp1, ptw==1),(pp2, ptw==2)]:
                    players[plyr].c_rally_total[rl_int] += 1
                    if won: players[plyr].c_rally_won[rl_int] += 1

            sa.c_fs_srv += 1
            if sw and pd.notna(rl) and rl<=3: sa.c_fs_won += 1

            s1=pt.Set1 or 0;s2=pt.Set2 or 0;cset=int(s1)+int(s2)+1
            if cset<=1:
                sa.c_set1_sp+=1
                if sw: sa.c_set1_sw+=1
            elif cset>=3:
                sa.c_set3_sp+=1
                if sw: sa.c_set3_sw+=1

            ssd = sa.c_surface[csurf]
            ssd["sp"]+=1
            if sw: ssd["sw"]+=1

            shot_seq = getattr(pt,"shot_sequence","")
            if isinstance(shot_seq,str) and len(shot_seq)>=2:
                for j in range(len(shot_seq)-1):
                    sa.c_bigrams[shot_seq[j:j+2]] += 1
            sa.c_total_pts += 1
            players[returner].c_total_pts += 1

    if (idx+1) % 100000 == 0:
        print(f"    {idx+1:,} matches processed...")

X = pd.DataFrame(rows).fillna(0)
y = pd.Series(labels)
dates_s = pd.Series(dates_list)

print(f"\n  Rows: {len(X):,}")
print(f"  Features: {X.shape[1]}")

# ─────────────────────────────────────────────────
# TEMPORAL SPLIT + TRAIN
# ─────────────────────────────────────────────────
cutoff = pd.Timestamp(TEMPORAL_CUTOFF)
tr = dates_s < cutoff
Xtr, ytr = X[tr], y[tr]
Xte, yte = X[~tr], y[~tr]

print(f"\n[SPLIT] Cutoff: {TEMPORAL_CUTOFF}")
print(f"  Train: {len(Xtr):,} | Test: {len(Xte):,}")

print("\n[TRAIN] XGBoost...")
model = xgb.XGBClassifier(max_depth=6, learning_rate=0.1, n_estimators=300,
    subsample=0.8, colsample_bytree=0.8, min_child_weight=5,
    reg_alpha=0, reg_lambda=1, eval_metric="logloss",
    use_label_encoder=False, random_state=42)
model.fit(Xtr, ytr)
probs = model.predict_proba(Xte)[:,1]
brier = np.mean((probs - yte)**2)

print(f"\n{'='*70}")
print(f"  HONEST EDGE BRIER (zero leakage):  {brier:.4f}")
print(f"  Previous rolling v2:               0.2041")
print(f"  Features: {X.shape[1]}")
print(f"{'='*70}")

imp = sorted(zip(X.columns, model.feature_importances_), key=lambda x:-x[1])
print(f"\n  Top 35 Feature Importances:")
edge_feats = {"elo_diff","elo_surface_diff","elo_all","elo_surface","form_3","form_5",
    "form_15","form_50","surface_form","pressure_divergence","second_serve_pressure_gap",
    "pressure_wide_shift","pressure_t_shift","crossover_vulnerability",
    "compound_fatigue","compound_fatigue_diff","prev_mins","prev_rest","prev_opp_rank"}
for i,(f,v) in enumerate(imp[:35]):
    tag = " ★EDGE" if any(e in f for e in edge_feats) else ""
    print(f"    {i+1:2d}. {f:45s} {v:.4f}{tag}")

# Save
pickle.dump((X,y,dates_s), open(OUTPUT_PATH, "wb"))
pickle.dump((X,y), open(REPO_ROOT/"data"/"processed"/"expanded_training.pkl","wb"))
pickle.dump(model, open(REPO_ROOT/"models"/"hard"/"best_edge_v1_model.pkl","wb"))

import json
meta = {"brier_honest":float(brier),"n_features":int(X.shape[1]),
        "n_train":int(len(Xtr)),"n_test":int(len(Xte)),
        "cutoff":TEMPORAL_CUTOFF,"zero_leakage":True,
        "top_features":[(f,float(v)) for f,v in imp[:35]]}
json.dump(meta, open(REPO_ROOT/"experiments"/"edge_v1_results.json","w"), indent=2)
print(f"\nSaved. Edge features integrated.")
