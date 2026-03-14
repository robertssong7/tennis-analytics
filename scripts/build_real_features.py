"""
TennisIQ Real Feature Engineering v3
Genuinely new features from existing data + temporal split.
1. Return game stats
2. First strike rate
3. Rally crossover point
4. Real fatigue (minutes, rest days, match load)
5. Match stage behavior (set 1 vs set 3+)
6. Opponent-adjusted stats (vs top 50)
7. Ball type encoding (from court_speed.csv)
8. Bo3 vs Bo5 behavioral tendencies per player
9. Surface-specific player stats
TEMPORAL SPLIT: train pre-2023, test 2023+
"""

import pandas as pd
import numpy as np
import pickle
import xgboost as xgb
from pathlib import Path
from collections import defaultdict
from datetime import timedelta

REPO_ROOT = Path(__file__).resolve().parent.parent
POINTS_PATH = REPO_ROOT / "data" / "processed" / "parsed_points.parquet"
UNI_PATH = REPO_ROOT / "data" / "processed" / "universal_features.parquet"
PROFILES_PATH = REPO_ROOT / "data" / "processed" / "player_profiles.parquet"
CPI_PATH = REPO_ROOT / "data" / "court_speed.csv"
OUTPUT_PATH = REPO_ROOT / "data" / "processed" / "expanded_training_v3.pkl"
TEMPORAL_CUTOFF = "2023-01-01"

print("=" * 70)
print("  TENNISIQ REAL FEATURE ENGINEERING v3")
print("=" * 70)

print("\n[LOAD] Loading data...")
points = pd.read_parquet(POINTS_PATH)
points.columns = points.columns.str.replace(" ", "_")
uni = pd.read_parquet(UNI_PATH)
profiles = pd.read_parquet(PROFILES_PATH)
print(f"  Points: {len(points):,}")
print(f"  Universal: {len(uni):,}")
print(f"  Profiles: {len(profiles)}")

uni["match_date"] = pd.to_datetime(uni["tourney_date"].astype(str), format="%Y%m%d", errors="coerce")
uni["minutes"] = pd.to_numeric(uni.get("minutes", 0), errors="coerce").fillna(0)

# Ball type from CPI
ball_type_map = {}
if CPI_PATH.exists():
    cpi_df = pd.read_csv(CPI_PATH)
    for _, row in cpi_df.iterrows():
        bt = str(row.get("ball_type", "")).strip()
        if bt and bt != "nan":
            ball_type_map[row.get("tournament", "")] = bt
    print(f"  Ball types loaded for {len(ball_type_map)} tournaments")

# Profile lookup
prof = {}
for _, row in profiles.iterrows():
    prof[row["player"]] = row.to_dict()

# ─────────────────────────────────────────────────
# FEATURE 1: RETURN GAME STATS
# ─────────────────────────────────────────────────
print("\n[1/9] Return game stats...")
player_return = defaultdict(lambda: {"rp":0,"rpw":0,"r1":0,"r1w":0,"r2":0,"r2w":0,"rw":0,"rue":0})

for i, pt in enumerate(points.itertuples(index=False)):
    svr = pt.Svr
    ptw = pt.PtWinner
    p1, p2 = pt.Player_1, pt.Player_2
    is2 = pd.notna(getattr(pt, "_6", None)) if hasattr(pt, "_6") else False
    try:
        is2 = pd.notna(pt._7) and str(pt._7).strip() != ""
    except:
        is2 = False

    if svr == 1:
        returner, ret_won = p2, (ptw == 2)
    elif svr == 2:
        returner, ret_won = p1, (ptw == 1)
    else:
        continue

    r = player_return[returner]
    r["rp"] += 1
    if ret_won: r["rpw"] += 1
    if is2:
        r["r2"] += 1
        if ret_won: r["r2w"] += 1
    else:
        r["r1"] += 1
        if ret_won: r["r1w"] += 1

    outcome = getattr(pt, "point_outcome", "")
    if ret_won and outcome in ("winner",): r["rw"] += 1
    if not ret_won and outcome == "unforced_error": r["rue"] += 1

    if i % 200000 == 0 and i > 0:
        print(f"    {i:,} points processed...")

return_stats = {}
for player, r in player_return.items():
    if r["rp"] < 50: continue
    return_stats[player] = {
        "return_pts_won_pct": r["rpw"] / r["rp"],
        "return_vs_1st_pct": r["r1w"] / max(r["r1"], 1),
        "return_vs_2nd_pct": r["r2w"] / max(r["r2"], 1),
        "return_aggression": r["rw"] / max(r["rw"] + r["rue"], 1),
    }
print(f"  {len(return_stats)} players")

# ─────────────────────────────────────────────────
# FEATURE 2: FIRST STRIKE RATE
# ─────────────────────────────────────────────────
print("\n[2/9] First strike rate...")
player_fs = defaultdict(lambda: {"sp":0,"fs":0})

for pt in points.itertuples(index=False):
    svr = pt.Svr
    ptw = pt.PtWinner
    p1, p2 = pt.Player_1, pt.Player_2
    rl = pt.rally_length

    if svr == 1: server, sw = p1, (ptw == 1)
    elif svr == 2: server, sw = p2, (ptw == 2)
    else: continue

    player_fs[server]["sp"] += 1
    if sw and pd.notna(rl) and rl <= 3:
        player_fs[server]["fs"] += 1

fs_stats = {}
for player, f in player_fs.items():
    if f["sp"] < 50: continue
    fs_stats[player] = {"first_strike_rate": f["fs"] / f["sp"]}
print(f"  {len(fs_stats)} players")

# ─────────────────────────────────────────────────
# FEATURE 3: RALLY CROSSOVER POINT
# ─────────────────────────────────────────────────
print("\n[3/9] Rally crossover...")
rally_bins = defaultdict(lambda: defaultdict(lambda: {"w":0,"t":0}))

for pt in points.itertuples(index=False):
    p1, p2 = pt.Player_1, pt.Player_2
    ptw = pt.PtWinner
    rl = pt.rally_length
    if pd.isna(rl) or rl < 1 or rl > 30: continue
    rl = int(rl)
    rally_bins[p1][rl]["t"] += 1
    rally_bins[p2][rl]["t"] += 1
    if ptw == 1: rally_bins[p1][rl]["w"] += 1
    else: rally_bins[p2][rl]["w"] += 1

cross_stats = {}
for player, bins in rally_bins.items():
    total = sum(b["t"] for b in bins.values())
    if total < 100: continue
    crossover = 1
    for rl in range(1, 20):
        b = bins.get(rl, {"w":0,"t":0})
        if b["t"] >= 10 and b["w"]/b["t"] >= 0.50:
            crossover = rl
    sw = sum(bins.get(r,{"w":0})["w"] for r in range(1,5))
    st = sum(bins.get(r,{"w":0,"t":0})["t"] for r in range(1,5))
    lw = sum(bins.get(r,{"w":0})["w"] for r in range(9,25))
    lt = sum(bins.get(r,{"w":0,"t":0})["t"] for r in range(9,25))
    cross_stats[player] = {
        "rally_crossover": crossover,
        "rally_wr_dropoff": (sw/max(st,1)) - (lw/max(lt,1)),
    }
print(f"  {len(cross_stats)} players")

# ─────────────────────────────────────────────────
# FEATURE 4: REAL FATIGUE
# ─────────────────────────────────────────────────
print("\n[4/9] Real fatigue metrics...")
player_hist = defaultdict(list)
for _, row in uni.iterrows():
    d = row["match_date"]
    if pd.isna(d): continue
    mins = row["minutes"]
    w, l = row.get("winner_name",""), row.get("loser_name","")
    if w: player_hist[w].append((d, mins))
    if l: player_hist[l].append((d, mins))
for p in player_hist:
    player_hist[p].sort(key=lambda x: x[0])

def fatigue(player, md):
    h = player_hist.get(player, [])
    if not h: return 0, 0, 30, 0, 0
    d14 = md - timedelta(days=14)
    d30 = md - timedelta(days=30)
    r14 = [(d,m) for d,m in h if d14<=d<md]
    r30 = [(d,m) for d,m in h if d30<=d<md]
    prev = [d for d,m in h if d<md]
    rest = (md - prev[-1]).days if prev else 30
    return sum(m for _,m in r14), len(r14), min(rest,60), sum(m for _,m in r30), len(r30)
print("  Done")

# ─────────────────────────────────────────────────
# FEATURE 5: MATCH STAGE BEHAVIOR
# ─────────────────────────────────────────────────
print("\n[5/9] Match stage behavior...")
player_stage = defaultdict(lambda: {"s1p":0,"s1w":0,"s3p":0,"s3w":0})

for pt in points.itertuples(index=False):
    svr = pt.Svr
    ptw = pt.PtWinner
    p1, p2 = pt.Player_1, pt.Player_2
    s1, s2 = pt.Set1 or 0, pt.Set2 or 0
    cset = int(s1) + int(s2) + 1

    if svr == 1: server, sw = p1, (ptw==1)
    elif svr == 2: server, sw = p2, (ptw==2)
    else: continue

    ps = player_stage[server]
    if cset <= 1:
        ps["s1p"] += 1
        if sw: ps["s1w"] += 1
    elif cset >= 3:
        ps["s3p"] += 1
        if sw: ps["s3w"] += 1

stage_stats = {}
for player, ps in player_stage.items():
    s1 = ps["s1w"]/max(ps["s1p"],1) if ps["s1p"]>30 else None
    s3 = ps["s3w"]/max(ps["s3p"],1) if ps["s3p"]>30 else None
    if s1 is not None and s3 is not None:
        stage_stats[player] = {"set1_serve_pct": s1, "late_match_dropoff": s1 - s3}
print(f"  {len(stage_stats)} players")

# ─────────────────────────────────────────────────
# FEATURE 6: OPPONENT-ADJUSTED STATS
# ─────────────────────────────────────────────────
print("\n[6/9] Opponent-adjusted stats...")
vs_top = defaultdict(lambda: {"w":0,"t":0})
vs_all = defaultdict(lambda: {"w":0,"t":0})

for _, row in uni.iterrows():
    w, l = row.get("winner_name",""), row.get("loser_name","")
    lr = pd.to_numeric(row.get("loser_rank",None), errors="coerce")
    wr = pd.to_numeric(row.get("winner_rank",None), errors="coerce")
    if not w or not l: continue
    vs_all[w]["w"]+=1; vs_all[w]["t"]+=1; vs_all[l]["t"]+=1
    if pd.notna(lr) and lr<=50: vs_top[w]["w"]+=1; vs_top[w]["t"]+=1
    if pd.notna(wr) and wr<=50: vs_top[l]["t"]+=1

opp_stats = {}
for player in set(list(vs_top.keys()) + list(vs_all.keys())):
    at = vs_all[player]["t"]
    if at < 10: continue
    awr = vs_all[player]["w"]/at
    tt = vs_top[player]["t"]
    twr = vs_top[player]["w"]/max(tt,1) if tt>=3 else awr
    opp_stats[player] = {"win_rate_vs_top50": twr, "top50_dropoff": awr - twr}
print(f"  {len(opp_stats)} players")

# ─────────────────────────────────────────────────
# FEATURE 7: BALL TYPE ENCODING
# ─────────────────────────────────────────────────
print("\n[7/9] Ball type encoding...")
ball_codes = {"Dunlop": 1, "Wilson": 2, "Penn": 3, "Babolat": 4, "Head": 5, "Slazenger": 6}
tourney_ball = {}
for t, bt in ball_type_map.items():
    for code_name, code in ball_codes.items():
        if code_name.lower() in bt.lower():
            tourney_ball[t] = code
            break
    else:
        tourney_ball[t] = 0
print(f"  {len(tourney_ball)} tournaments with ball type")

# ─────────────────────────────────────────────────
# FEATURE 8: BO3 vs BO5 TENDENCIES PER PLAYER
# ─────────────────────────────────────────────────
print("\n[8/9] Bo3 vs Bo5 per-player tendencies...")
player_format = defaultdict(lambda: {"bo3_sp":0,"bo3_sw":0,"bo5_sp":0,"bo5_sw":0,
    "bo3_agg_w":0,"bo3_agg_d":0,"bo5_agg_w":0,"bo5_agg_d":0})

for pt in points.itertuples(index=False):
    svr = pt.Svr
    ptw = pt.PtWinner
    p1, p2 = pt.Player_1, pt.Player_2
    bo = getattr(pt, "Best_of", 3)
    try: bo = int(bo)
    except: bo = 3
    outcome = getattr(pt, "point_outcome", "")

    if svr == 1: server, sw = p1, (ptw==1)
    elif svr == 2: server, sw = p2, (ptw==2)
    else: continue

    pf = player_format[server]
    if bo == 5:
        pf["bo5_sp"] += 1
        if sw: pf["bo5_sw"] += 1
        if outcome in ("winner","ace"): pf["bo5_agg_w"] += 1
        if outcome in ("winner","ace","unforced_error"): pf["bo5_agg_d"] += 1
    else:
        pf["bo3_sp"] += 1
        if sw: pf["bo3_sw"] += 1
        if outcome in ("winner","ace"): pf["bo3_agg_w"] += 1
        if outcome in ("winner","ace","unforced_error"): pf["bo3_agg_d"] += 1

format_stats = {}
for player, pf in player_format.items():
    bo3_wr = pf["bo3_sw"]/max(pf["bo3_sp"],1) if pf["bo3_sp"]>50 else None
    bo5_wr = pf["bo5_sw"]/max(pf["bo5_sp"],1) if pf["bo5_sp"]>50 else None
    bo3_agg = pf["bo3_agg_w"]/max(pf["bo3_agg_d"],1) if pf["bo3_agg_d"]>20 else None
    bo5_agg = pf["bo5_agg_w"]/max(pf["bo5_agg_d"],1) if pf["bo5_agg_d"]>20 else None
    if bo3_wr is not None:
        format_stats[player] = {
            "bo3_serve_wr": bo3_wr,
            "bo5_serve_wr": bo5_wr or bo3_wr,
            "bo3_aggression": bo3_agg or 0.5,
            "bo5_aggression": bo5_agg or (bo3_agg or 0.5),
            "format_serve_diff": (bo5_wr or bo3_wr) - bo3_wr,
            "format_agg_diff": (bo5_agg or bo3_agg or 0.5) - (bo3_agg or 0.5),
        }
print(f"  {len(format_stats)} players")

# ─────────────────────────────────────────────────
# FEATURE 9: SURFACE-SPECIFIC PLAYER STATS
# ─────────────────────────────────────────────────
print("\n[9/9] Surface-specific player stats...")
player_surface = defaultdict(lambda: defaultdict(lambda: {"sp":0,"sw":0,"aw":0,"ad":0,"rl":[]}))

for pt in points.itertuples(index=False):
    svr = pt.Svr
    ptw = pt.PtWinner
    p1, p2 = pt.Player_1, pt.Player_2
    surface = getattr(pt, "Surface", "Hard")
    if surface not in ("Hard","Clay","Grass"): continue
    outcome = getattr(pt, "point_outcome", "")
    rl = pt.rally_length

    if svr == 1: server, sw = p1, (ptw==1)
    elif svr == 2: server, sw = p2, (ptw==2)
    else: continue

    ps = player_surface[server][surface]
    ps["sp"] += 1
    if sw: ps["sw"] += 1
    if outcome in ("winner","ace"): ps["aw"] += 1
    if outcome in ("winner","ace","unforced_error"): ps["ad"] += 1
    if pd.notna(rl): ps["rl"].append(rl)

surf_stats = {}
for player, surfs in player_surface.items():
    ss = {}
    for surface in ("Hard","Clay","Grass"):
        d = surfs.get(surface, {"sp":0,"sw":0,"aw":0,"ad":0,"rl":[]})
        if d["sp"] < 50: continue
        ss[surface] = {
            "serve_wr": d["sw"]/d["sp"],
            "aggression": d["aw"]/max(d["ad"],1) if d["ad"]>20 else 0.5,
            "avg_rally": np.mean(d["rl"]) if d["rl"] else 4.5,
        }
    if ss:
        surf_stats[player] = ss
print(f"  {len(surf_stats)} players with surface data")

# ─────────────────────────────────────────────────
# BUILD TRAINING MATRIX
# ─────────────────────────────────────────────────
print("\n[BUILD] Constructing training matrix...")

def get_pf(name, surface="Hard"):
    """Get all player features."""
    p = prof.get(name, {})
    ret = return_stats.get(name, {})
    fs = fs_stats.get(name, {})
    co = cross_stats.get(name, {})
    st = stage_stats.get(name, {})
    oa = opp_stats.get(name, {})
    fmt = format_stats.get(name, {})
    ss = surf_stats.get(name, {}).get(surface, {})
    return {
        "serve_wide_pct": p.get("serve_wide_pct", 0.4),
        "serve_body_pct": p.get("serve_body_pct", 0.2),
        "serve_t_pct": p.get("serve_t_pct", 0.35),
        "serve_dir_entropy": p.get("serve_dir_entropy", 1.4),
        "serve_wide_pct_pressure": p.get("serve_wide_pct_pressure", 0.4),
        "serve_t_pct_pressure": p.get("serve_t_pct_pressure", 0.35),
        "serve_dir_entropy_pressure": p.get("serve_dir_entropy_pressure", 1.4),
        "serve_pressure_kl": p.get("serve_pressure_kl", 0.01),
        "avg_rally_len_serving": p.get("avg_rally_len_serving", 4.5),
        "short_rally_pct_serving": p.get("short_rally_pct_serving", 0.4),
        "long_rally_pct_serving": p.get("long_rally_pct_serving", 0.15),
        "win_rate_short_rally": p.get("win_rate_short_rally", 0.65),
        "win_rate_long_rally": p.get("win_rate_long_rally", 0.50),
        "aggression_index": p.get("aggression_index", 0.50),
        "ace_rate": p.get("ace_rate", 0.04),
        "pattern_diversity_2gram": p.get("pattern_diversity_2gram", 4.0),
        "pattern_diversity_3gram": p.get("pattern_diversity_3gram", 6.0),
        "win_rate_far_behind": p.get("win_rate_far_behind", 0.45),
        "win_rate_far_ahead": p.get("win_rate_far_ahead", 0.70),
        # Return game
        "return_pts_won_pct": ret.get("return_pts_won_pct", 0.38),
        "return_vs_1st_pct": ret.get("return_vs_1st_pct", 0.30),
        "return_vs_2nd_pct": ret.get("return_vs_2nd_pct", 0.50),
        "return_aggression": ret.get("return_aggression", 0.50),
        # First strike
        "first_strike_rate": fs.get("first_strike_rate", 0.25),
        # Rally crossover
        "rally_crossover": co.get("rally_crossover", 6),
        "rally_wr_dropoff": co.get("rally_wr_dropoff", 0.15),
        # Match stage
        "set1_serve_pct": st.get("set1_serve_pct", 0.63),
        "late_match_dropoff": st.get("late_match_dropoff", 0.02),
        # Opponent-adjusted
        "win_rate_vs_top50": oa.get("win_rate_vs_top50", 0.40),
        "top50_dropoff": oa.get("top50_dropoff", 0.10),
        # Format
        "bo3_serve_wr": fmt.get("bo3_serve_wr", 0.63),
        "bo5_serve_wr": fmt.get("bo5_serve_wr", 0.63),
        "bo3_aggression": fmt.get("bo3_aggression", 0.50),
        "bo5_aggression": fmt.get("bo5_aggression", 0.50),
        "format_serve_diff": fmt.get("format_serve_diff", 0.0),
        "format_agg_diff": fmt.get("format_agg_diff", 0.0),
        # Surface-specific
        "surface_serve_wr": ss.get("serve_wr", p.get("win_rate_short_rally", 0.63)),
        "surface_aggression": ss.get("aggression", p.get("aggression_index", 0.50)),
        "surface_avg_rally": ss.get("avg_rally", p.get("avg_rally_len_serving", 4.5)),
    }

valid = uni[uni["match_date"].notna() & uni["winner_name"].notna() & uni["loser_name"].notna()].copy()
print(f"  Valid matches: {len(valid):,}")

surface_map = {"Hard":1,"Clay":2,"Grass":3,"Carpet":4}
level_map = {"G":4,"M":3,"A":2,"D":1,"F":5}

rows = []
labels = []
dates_list = []

for i, (_, match) in enumerate(valid.iterrows()):
    winner = match["winner_name"]
    loser = match["loser_name"]
    md = match["match_date"]
    surface = match.get("surface", "Hard")

    sc = surface_map.get(surface, 1)
    lc = level_map.get(str(match.get("tourney_level","A")), 2)
    bo = int(match.get("best_of", 3) or 3)
    cpi_val = float(match.get("cpi", 0)) if "cpi" in match.index else 0
    rd = (pd.to_numeric(match.get("loser_rank",500), errors="coerce") or 500) - \
         (pd.to_numeric(match.get("winner_rank",500), errors="coerce") or 500)
    wf = float(match.get("winner_recent_form", 0.5) or 0.5)
    lf = float(match.get("loser_recent_form", 0.5) or 0.5)
    wh = float(match.get("winner_h2h_pct", 0.5) or 0.5)
    lh = float(match.get("loser_h2h_pct", 0.5) or 0.5)

    # Ball type
    tname = match.get("tourney_name", "")
    bt = tourney_ball.get(tname, 0)

    # Player features
    wpf = get_pf(winner, surface)
    lpf = get_pf(loser, surface)

    # Fatigue
    wfat = fatigue(winner, md)
    lfat = fatigue(loser, md)

    base = {"surface_code":sc, "tourney_level_code":lc, "best_of":bo,
            "cpi":cpi_val, "ball_type":bt, "rank_diff":rd,
            "p1_recent_form":wf, "p2_recent_form":lf,
            "p1_h2h_pct":wh, "p2_h2h_pct":lh}

    # Row 1: winner = p1
    r1 = dict(base)
    for k,v in wpf.items(): r1[f"p1_{k}"] = v
    for k,v in lpf.items(): r1[f"p2_{k}"] = v
    r1["p1_mins_14d"]=wfat[0]; r1["p2_mins_14d"]=lfat[0]
    r1["p1_matches_14d"]=wfat[1]; r1["p2_matches_14d"]=lfat[1]
    r1["p1_days_rest"]=wfat[2]; r1["p2_days_rest"]=lfat[2]
    r1["p1_mins_30d"]=wfat[3]; r1["p2_mins_30d"]=lfat[3]
    r1["fatigue_mins_diff"]=wfat[0]-lfat[0]
    r1["rest_days_diff"]=wfat[2]-lfat[2]
    rows.append(r1)
    labels.append(1)
    dates_list.append(md)

    # Row 2: loser = p1
    r2 = dict(base)
    r2["rank_diff"] = -rd
    r2["p1_recent_form"]=lf; r2["p2_recent_form"]=wf
    r2["p1_h2h_pct"]=lh; r2["p2_h2h_pct"]=wh
    for k,v in lpf.items(): r2[f"p1_{k}"] = v
    for k,v in wpf.items(): r2[f"p2_{k}"] = v
    r2["p1_mins_14d"]=lfat[0]; r2["p2_mins_14d"]=wfat[0]
    r2["p1_matches_14d"]=lfat[1]; r2["p2_matches_14d"]=wfat[1]
    r2["p1_days_rest"]=lfat[2]; r2["p2_days_rest"]=wfat[2]
    r2["p1_mins_30d"]=lfat[3]; r2["p2_mins_30d"]=wfat[3]
    r2["fatigue_mins_diff"]=lfat[0]-wfat[0]
    r2["rest_days_diff"]=lfat[2]-wfat[2]
    rows.append(r2)
    labels.append(0)
    dates_list.append(md)

    if (i+1) % 100000 == 0:
        print(f"  {i+1:,} matches processed...")

X = pd.DataFrame(rows).fillna(0)
y = pd.Series(labels)
dates_s = pd.Series(dates_list)

print(f"\n  Total rows: {len(X):,}")
print(f"  Total features: {X.shape[1]}")

# ─────────────────────────────────────────────────
# TEMPORAL SPLIT + TRAIN
# ─────────────────────────────────────────────────
print(f"\n[SPLIT] Temporal cutoff: {TEMPORAL_CUTOFF}")
cutoff = pd.Timestamp(TEMPORAL_CUTOFF)
tr = dates_s < cutoff
te = dates_s >= cutoff
Xtr, ytr = X[tr], y[tr]
Xte, yte = X[te], y[te]
print(f"  Train: {len(Xtr):,} rows ({tr.sum()//2:,} matches)")
print(f"  Test:  {len(Xte):,} rows ({te.sum()//2:,} matches)")

print("\n[TRAIN] XGBoost temporal split...")
params = {"max_depth":6,"learning_rate":0.1,"n_estimators":300,
          "subsample":0.8,"colsample_bytree":0.8,"min_child_weight":5,
          "reg_alpha":0,"reg_lambda":1,"eval_metric":"logloss",
          "use_label_encoder":False}

model = xgb.XGBClassifier(**params, random_state=42)
model.fit(Xtr, ytr)
probs = model.predict_proba(Xte)[:,1]
brier_temporal = np.mean((probs - yte)**2)

# CV for comparison
from sklearn.model_selection import cross_val_predict
model_cv = xgb.XGBClassifier(**params, random_state=42)
cv_probs = cross_val_predict(model_cv, X, y, cv=5, method="predict_proba")[:,1]
brier_cv = np.mean((cv_probs - y)**2)

print(f"\n{'='*70}")
print(f"  RESULTS")
print(f"  Temporal Brier (HONEST):   {brier_temporal:.4f}")
print(f"  5-fold CV Brier (compare): {brier_cv:.4f}")
print(f"  Previous best (random CV): 0.2115")
print(f"  Features: {X.shape[1]}")
print(f"{'='*70}")

# Feature importances
imp = sorted(zip(X.columns, model.feature_importances_), key=lambda x:-x[1])
print(f"\n  Top 25 Feature Importances:")
new_feats = {"return_pts_won_pct","return_vs_1st_pct","return_vs_2nd_pct",
    "return_aggression","first_strike_rate","rally_crossover","rally_wr_dropoff",
    "set1_serve_pct","late_match_dropoff","win_rate_vs_top50","top50_dropoff",
    "mins_14d","matches_14d","days_rest","rest_days_diff","fatigue_mins_diff",
    "mins_30d","ball_type","bo3_serve_wr","bo5_serve_wr","bo3_aggression",
    "bo5_aggression","format_serve_diff","format_agg_diff","surface_serve_wr",
    "surface_aggression","surface_avg_rally"}
for i,(f,v) in enumerate(imp[:25]):
    tag = " *NEW*" if any(f.endswith(n) for n in new_feats) else ""
    print(f"    {i+1:2d}. {f:40s} {v:.4f}{tag}")

# Save
pickle.dump((X,y), open(OUTPUT_PATH, "wb"))
# Also overwrite main training pkl
pickle.dump((X,y), open(REPO_ROOT/"data"/"processed"/"expanded_training.pkl","wb"))
pickle.dump(model, open(REPO_ROOT/"models"/"hard"/"best_temporal_model.pkl","wb"))

import json
meta = {"brier_temporal":float(brier_temporal),"brier_cv":float(brier_cv),
        "n_features":int(X.shape[1]),"n_train":int(len(Xtr)),"n_test":int(len(Xte)),
        "cutoff":TEMPORAL_CUTOFF,
        "top_features":[(f,float(v)) for f,v in imp[:25]]}
json.dump(meta, open(REPO_ROOT/"experiments"/"temporal_v3_results.json","w"), indent=2)

print(f"\nSaved training data, model, and results.")
print("Done.")
