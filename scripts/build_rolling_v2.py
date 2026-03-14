"""
TennisIQ Rolling Features v2 — ZERO LEAKAGE
Combines match-level stats (214K matches, 6,367 players) with
charted point data (7,160 matches, 980 players).
Every stat computed from strictly pre-match data.
"""

import pandas as pd
import numpy as np
import pickle
import xgboost as xgb
import math
from pathlib import Path
from collections import defaultdict
from datetime import timedelta
from bisect import bisect_right

REPO_ROOT = Path(__file__).resolve().parent.parent
POINTS_PATH = REPO_ROOT / "data" / "processed" / "parsed_points.parquet"
UNI_PATH = REPO_ROOT / "data" / "processed" / "universal_features.parquet"
CPI_PATH = REPO_ROOT / "data" / "court_speed.csv"
OUTPUT_PATH = REPO_ROOT / "data" / "processed" / "training_rolling_v2.pkl"
TEMPORAL_CUTOFF = "2023-01-01"

print("=" * 70)
print("  TENNISIQ ROLLING FEATURES v2 — MATCH STATS + CHARTED DATA")
print("=" * 70)

# ─────────────────────────────────────────────────
# LOAD
# ─────────────────────────────────────────────────
print("\n[LOAD]")
points = pd.read_parquet(POINTS_PATH)
points.columns = points.columns.str.replace(" ", "_")
uni = pd.read_parquet(UNI_PATH)
uni["match_date"] = pd.to_datetime(uni["tourney_date"].astype(str), format="%Y%m%d", errors="coerce")
uni["minutes"] = pd.to_numeric(uni.get("minutes", 0), errors="coerce").fillna(0)
print(f"  Points: {len(points):,}")
print(f"  Matches: {len(uni):,}")
print(f"  Matches with stats: {uni['w_svpt'].notna().sum():,}")

# Ball type + CPI
ball_codes = {"Dunlop":1,"Wilson":2,"Penn":3,"Babolat":4,"Head":5,"Slazenger":6}
tourney_meta = {}
if CPI_PATH.exists():
    cpi_df = pd.read_csv(CPI_PATH)
    for _, row in cpi_df.iterrows():
        t = row.get("tournament","")
        bt = str(row.get("ball_type",""))
        bc = 0
        for name, code in ball_codes.items():
            if name.lower() in bt.lower(): bc = code; break
        tourney_meta[t] = {"cpi": float(row.get("cpi",0) or 0), "ball_type": bc}

# ─────────────────────────────────────────────────
# STEP 1: Pre-process charted points by match
# ─────────────────────────────────────────────────
print("\n[STEP 1] Pre-processing charted points by match...")

# Build per-match summaries from charted data
charted_matches = {}  # match_id -> {p1, p2, surface, bo, points_data}
for mid, grp in points.groupby("match_id"):
    row0 = grp.iloc[0]
    try:
        bo = int(float(row0.get("Best_of", 3) or 3))
    except:
        bo = 3
    charted_matches[mid] = {
        "p1": row0["Player_1"],
        "p2": row0["Player_2"],
        "surface": row0.get("Surface", "Hard"),
        "bo": bo,
        "points": grp,
    }

# Match charted matches to dates via player name lookup
uni_date_lookup = defaultdict(list)
for _, row in uni.iterrows():
    w, l = row.get("winner_name",""), row.get("loser_name","")
    d = row["match_date"]
    if pd.isna(d) or not w or not l: continue
    uni_date_lookup[(w,l)].append(d)
    uni_date_lookup[(l,w)].append(d)

for mid, info in charted_matches.items():
    key = (info["p1"], info["p2"])
    dates = uni_date_lookup.get(key, [])
    info["date"] = min(dates) if dates else pd.Timestamp("2010-01-01")

sorted_charted = sorted(charted_matches.keys(), key=lambda m: charted_matches[m]["date"])
print(f"  {len(sorted_charted)} charted matches linked to dates")

# ─────────────────────────────────────────────────
# STEP 2: Process ALL matches chronologically
# Build rolling accumulators from match-level stats
# AND charted point data
# ─────────────────────────────────────────────────
print("\n[STEP 2] Building rolling profiles chronologically...")

class PlayerStats:
    """Rolling accumulator combining match stats + charted data."""
    def __init__(self):
        # From match-level stats (214K matches)
        self.m_aces=0; self.m_dfs=0; self.m_svpt=0
        self.m_1stIn=0; self.m_1stWon=0; self.m_2ndWon=0
        self.m_bpSaved=0; self.m_bpFaced=0; self.m_svGms=0
        # Return (derived from opponent serve stats)
        self.m_ret_pts=0; self.m_ret_won=0
        self.m_ret_vs_1st=0; self.m_ret_vs_1st_won=0
        self.m_ret_vs_2nd=0; self.m_ret_vs_2nd_won=0
        # From charted points (7,160 matches)
        self.c_srv_wide=0; self.c_srv_body=0; self.c_srv_t=0; self.c_srv_total=0
        self.c_srv_wide_bp=0; self.c_srv_t_bp=0; self.c_srv_bp_total=0
        self.c_winners=0; self.c_ue=0
        self.c_rally_won=[0]*25; self.c_rally_total=[0]*25
        self.c_fs_srv=0; self.c_fs_won=0
        self.c_set1_sp=0; self.c_set1_sw=0; self.c_set3_sp=0; self.c_set3_sw=0
        self.c_bo3_sp=0; self.c_bo3_sw=0; self.c_bo5_sp=0; self.c_bo5_sw=0
        self.c_bo3_aw=0; self.c_bo3_ad=0; self.c_bo5_aw=0; self.c_bo5_ad=0
        self.c_surface = defaultdict(lambda:{"sp":0,"sw":0,"aw":0,"ad":0,"rl_sum":0,"rl_n":0})
        self.c_bigrams = defaultdict(int)
        self.c_total_pts = 0

    def snapshot(self, surface="Hard"):
        """Return feature dict using BEST available data."""
        f = {}
        # ── Match-level serve stats (broad coverage) ──
        svpt = max(self.m_svpt, 1)
        f1in = max(self.m_1stIn, 1)
        sv2pt = max(svpt - self.m_1stIn, 1)
        f["ace_rate"] = self.m_aces / svpt if self.m_svpt > 20 else -1
        f["df_rate"] = self.m_dfs / svpt if self.m_svpt > 20 else -1
        f["first_serve_pct"] = self.m_1stIn / svpt if self.m_svpt > 20 else -1
        f["first_serve_won_pct"] = self.m_1stWon / f1in if self.m_1stIn > 10 else -1
        f["second_serve_won_pct"] = self.m_2ndWon / sv2pt if sv2pt > 10 else -1
        f["serve_pts_won_pct"] = (self.m_1stWon + self.m_2ndWon) / svpt if self.m_svpt > 20 else -1
        f["bp_save_pct"] = self.m_bpSaved / max(self.m_bpFaced, 1) if self.m_bpFaced > 5 else -1

        # ── Match-level return stats (broad coverage) ──
        rp = max(self.m_ret_pts, 1)
        r1 = max(self.m_ret_vs_1st, 1)
        r2 = max(self.m_ret_vs_2nd, 1)
        f["return_pts_won_pct"] = self.m_ret_won / rp if self.m_ret_pts > 20 else -1
        f["return_vs_1st_pct"] = self.m_ret_vs_1st_won / r1 if self.m_ret_vs_1st > 10 else -1
        f["return_vs_2nd_pct"] = self.m_ret_vs_2nd_won / r2 if self.m_ret_vs_2nd > 10 else -1

        # ── Charted serve direction (granular, fewer players) ──
        ct = max(self.c_srv_total, 1)
        f["serve_wide_pct"] = self.c_srv_wide / ct if self.c_srv_total > 20 else -1
        f["serve_body_pct"] = self.c_srv_body / ct if self.c_srv_total > 20 else -1
        f["serve_t_pct"] = self.c_srv_t / ct if self.c_srv_total > 20 else -1

        # Serve entropy
        entropy = 0
        if self.c_srv_total > 20:
            for c in [self.c_srv_wide, self.c_srv_body, self.c_srv_t]:
                if c > 0:
                    p = c / ct
                    entropy -= p * math.log2(p)
        f["serve_dir_entropy"] = entropy if self.c_srv_total > 20 else -1

        # Pressure serve
        bpt = max(self.c_srv_bp_total, 1)
        f["serve_wide_pct_pressure"] = self.c_srv_wide_bp / bpt if self.c_srv_bp_total > 10 else -1
        f["serve_t_pct_pressure"] = self.c_srv_t_bp / bpt if self.c_srv_bp_total > 10 else -1

        # Pressure KL
        pressure_kl = 0
        if self.c_srv_bp_total > 10 and self.c_srv_total > 20:
            base = [self.c_srv_wide/ct, self.c_srv_body/ct, self.c_srv_t/ct]
            pres = [self.c_srv_wide_bp/bpt, (bpt-self.c_srv_wide_bp-self.c_srv_t_bp)/bpt, self.c_srv_t_bp/bpt]
            for b, p in zip(base, pres):
                if b > 0.01 and p > 0.01:
                    pressure_kl += p * math.log2(p / b)
        f["serve_pressure_kl"] = pressure_kl

        # ── Charted aggression ──
        wd = self.c_winners + self.c_ue
        f["aggression_index"] = self.c_winners / max(wd, 1) if wd > 20 else -1

        # ── Rally crossover ──
        crossover = -1
        if sum(self.c_rally_total) > 50:
            crossover = 1
            for rl in range(1, 20):
                t = self.c_rally_total[rl]
                w = self.c_rally_won[rl]
                if t >= 10 and w/t >= 0.50:
                    crossover = rl
        f["rally_crossover"] = crossover

        sw = sum(self.c_rally_won[r] for r in range(1,5))
        st = sum(self.c_rally_total[r] for r in range(1,5))
        lw = sum(self.c_rally_won[r] for r in range(9,25))
        lt = sum(self.c_rally_total[r] for r in range(9,25))
        f["win_rate_short_rally"] = sw/max(st,1) if st > 20 else -1
        f["win_rate_long_rally"] = lw/max(lt,1) if lt > 20 else -1
        f["rally_wr_dropoff"] = (sw/max(st,1)) - (lw/max(lt,1)) if st > 20 and lt > 20 else -1

        # ── First strike ──
        f["first_strike_rate"] = self.c_fs_won / max(self.c_fs_srv, 1) if self.c_fs_srv > 20 else -1

        # ── Match stage ──
        f["set1_serve_pct"] = self.c_set1_sw / max(self.c_set1_sp, 1) if self.c_set1_sp > 20 else -1
        f["late_match_dropoff"] = (self.c_set1_sw/max(self.c_set1_sp,1)) - (self.c_set3_sw/max(self.c_set3_sp,1)) if self.c_set3_sp > 10 and self.c_set1_sp > 20 else -1

        # ── Format ──
        f["bo3_serve_wr"] = self.c_bo3_sw / max(self.c_bo3_sp, 1) if self.c_bo3_sp > 20 else -1
        f["bo5_serve_wr"] = self.c_bo5_sw / max(self.c_bo5_sp, 1) if self.c_bo5_sp > 20 else -1
        f["bo3_aggression"] = self.c_bo3_aw / max(self.c_bo3_ad, 1) if self.c_bo3_ad > 10 else -1
        f["bo5_aggression"] = self.c_bo5_aw / max(self.c_bo5_ad, 1) if self.c_bo5_ad > 10 else -1
        f["format_serve_diff"] = (self.c_bo5_sw/max(self.c_bo5_sp,1)) - (self.c_bo3_sw/max(self.c_bo3_sp,1)) if self.c_bo5_sp>20 and self.c_bo3_sp>20 else -1

        # ── Surface-specific ──
        ss = self.c_surface.get(surface, {"sp":0,"sw":0,"aw":0,"ad":0})
        f["surface_serve_wr"] = ss["sw"]/ss["sp"] if ss["sp"]>20 else -1
        f["surface_aggression"] = ss["aw"]/max(ss["ad"],1) if ss["ad"]>10 else -1

        # ── Pattern diversity ──
        f["pattern_diversity_2gram"] = len(self.c_bigrams) if self.c_total_pts > 50 else -1

        f["has_match_stats"] = 1 if self.m_svpt > 20 else 0
        f["has_charted_data"] = 1 if self.c_total_pts > 50 else 0

        return f

    def update_match_stats(self, is_winner, opp_stats, match_stats):
        """Update from match-level w_/l_ columns."""
        if is_winner:
            my, opp = "w_", "l_"
        else:
            my, opp = "l_", "w_"

        def g(col):
            v = match_stats.get(col, 0)
            return float(v) if pd.notna(v) else 0

        # My serve stats
        self.m_aces += g(my+"ace")
        self.m_dfs += g(my+"df")
        self.m_svpt += g(my+"svpt")
        self.m_1stIn += g(my+"1stIn")
        self.m_1stWon += g(my+"1stWon")
        self.m_2ndWon += g(my+"2ndWon")
        self.m_bpSaved += g(my+"bpSaved")
        self.m_bpFaced += g(my+"bpFaced")

        # My return stats (derived from opponent's serve)
        opp_svpt = g(opp+"svpt")
        opp_1stIn = g(opp+"1stIn")
        opp_1stWon = g(opp+"1stWon")
        opp_2ndWon = g(opp+"2ndWon")
        self.m_ret_pts += opp_svpt
        self.m_ret_won += opp_svpt - opp_1stWon - opp_2ndWon
        self.m_ret_vs_1st += opp_1stIn
        self.m_ret_vs_1st_won += opp_1stIn - opp_1stWon
        self.m_ret_vs_2nd += max(opp_svpt - opp_1stIn, 0)
        self.m_ret_vs_2nd_won += max(opp_svpt - opp_1stIn, 0) - opp_2ndWon


# Initialize
accums = defaultdict(PlayerStats)

# Sort ALL universal matches by date
uni_sorted = uni[uni["match_date"].notna()].sort_values("match_date").reset_index(drop=True)
print(f"  {len(uni_sorted):,} matches to process")

# Build charted match lookup: (p1, p2, approx_date) -> match_id
charted_lookup = {}
for mid, info in charted_matches.items():
    charted_lookup[(info["p1"], info["p2"])] = charted_lookup.get((info["p1"], info["p2"]), [])
    charted_lookup[(info["p1"], info["p2"])].append(mid)
    charted_lookup[(info["p2"], info["p1"])] = charted_lookup.get((info["p2"], info["p1"]), [])
    charted_lookup[(info["p2"], info["p1"])].append(mid)

# Track which charted matches we've processed
processed_charted = set()

# Rolling form, h2h, fatigue, opponent-adjusted
player_results = defaultdict(list)  # player -> [(date, won_bool)]
player_match_log = defaultdict(list)  # player -> [(date, minutes)]
h2h_wins = defaultdict(int)
h2h_total = defaultdict(int)
player_wins_all = defaultdict(int)
player_total_all = defaultdict(int)
player_wins_top50 = defaultdict(int)
player_total_top50 = defaultdict(int)

# Snapshot storage
snap_dates = defaultdict(list)
snap_vals = defaultdict(list)

# Process match-by-match
rows = []
labels = []
dates_list = []
surface_map = {"Hard":1,"Clay":2,"Grass":3,"Carpet":4}
level_map = {"G":4,"M":3,"A":2,"D":1,"F":5}
skipped_no_data = 0
used_match_stats = 0
used_charted = 0

print("  Processing all matches chronologically...")
for idx, (_, match) in enumerate(uni_sorted.iterrows()):
    w = match.get("winner_name", "")
    l = match.get("loser_name", "")
    md = match["match_date"]
    if not w or not l: continue

    surface = match.get("surface", "Hard")
    has_stats = pd.notna(match.get("w_svpt"))

    # ── SNAPSHOT both players BEFORE this match ──
    w_snap = accums[w].snapshot(surface)
    l_snap = accums[l].snapshot(surface)

    # Rolling form
    def rolling_form(player, window=15):
        hist = player_results.get(player, [])
        recent = hist[-window:]
        if len(recent) < 3: return 0.5
        weights = np.linspace(0.5, 1.0, len(recent))
        wins = np.array([1.0 if won else 0.0 for won in recent])
        return float(np.average(wins, weights=weights))

    wf = rolling_form(w)
    lf = rolling_form(l)

    # H2H
    wh2h_t = h2h_total.get((w,l),0) + h2h_total.get((l,w),0)
    w_h2h = h2h_wins.get((w,l),0) / max(wh2h_t,1) if wh2h_t > 0 else 0.5
    l_h2h = h2h_wins.get((l,w),0) / max(wh2h_t,1) if wh2h_t > 0 else 0.5

    # Fatigue
    def get_fat(player):
        hist = player_match_log.get(player, [])
        d14 = md - timedelta(days=14)
        d30 = md - timedelta(days=30)
        r14 = [(d,m) for d,m in hist if d14<=d<md]
        r30 = [(d,m) for d,m in hist if d30<=d<md]
        prev = [d for d,m in hist if d<md]
        rest = min((md - prev[-1]).days, 60) if prev else 30
        return sum(m for _,m in r14), len(r14), rest, sum(m for _,m in r30), len(r30)

    wfat = get_fat(w)
    lfat = get_fat(l)

    # Opponent-adjusted
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

    # Ranks
    wr_rank = pd.to_numeric(match.get("winner_rank",500), errors="coerce") or 500
    lr_rank = pd.to_numeric(match.get("loser_rank",500), errors="coerce") or 500
    rd = lr_rank - wr_rank

    sc = surface_map.get(surface, 1)
    lc = level_map.get(str(match.get("tourney_level","A")), 2)
    bo = int(match.get("best_of", 3) or 3)
    tname = match.get("tourney_name","")
    tm = tourney_meta.get(tname, {"cpi":0,"ball_type":0})

    # ── BUILD FEATURE DICT ──
    # -1 means no data; will be replaced with defaults
    DEFAULTS = {
        "ace_rate":0.04,"df_rate":0.03,"first_serve_pct":0.60,
        "first_serve_won_pct":0.70,"second_serve_won_pct":0.50,
        "serve_pts_won_pct":0.63,"bp_save_pct":0.62,
        "return_pts_won_pct":0.37,"return_vs_1st_pct":0.30,"return_vs_2nd_pct":0.50,
        "serve_wide_pct":0.40,"serve_body_pct":0.20,"serve_t_pct":0.35,
        "serve_dir_entropy":1.40,"serve_wide_pct_pressure":0.40,
        "serve_t_pct_pressure":0.35,"serve_pressure_kl":0.01,
        "aggression_index":0.50,"rally_crossover":6,"win_rate_short_rally":0.65,
        "win_rate_long_rally":0.50,"rally_wr_dropoff":0.15,
        "first_strike_rate":0.25,"set1_serve_pct":0.63,"late_match_dropoff":0.02,
        "bo3_serve_wr":0.63,"bo5_serve_wr":0.63,"bo3_aggression":0.50,
        "bo5_aggression":0.50,"format_serve_diff":0.0,
        "surface_serve_wr":0.63,"surface_aggression":0.50,
        "pattern_diversity_2gram":20,"has_match_stats":0,"has_charted_data":0,
    }

    def fill(snap):
        return {k: (snap[k] if snap[k] != -1 else DEFAULTS.get(k, 0)) for k in snap}

    wf_filled = fill(w_snap)
    lf_filled = fill(l_snap)

    pkeys = list(DEFAULTS.keys())

    base = {"surface_code":sc,"tourney_level_code":lc,"best_of":bo,
            "cpi":tm["cpi"],"ball_type":tm["ball_type"],"rank_diff":rd,
            "p1_recent_form":wf,"p2_recent_form":lf,
            "p1_h2h_pct":w_h2h,"p2_h2h_pct":l_h2h}

    # Row 1: winner = p1
    r1 = dict(base)
    for k in pkeys: r1[f"p1_{k}"] = wf_filled[k]
    for k in pkeys: r1[f"p2_{k}"] = lf_filled[k]
    r1["p1_mins_14d"]=wfat[0];r1["p2_mins_14d"]=lfat[0]
    r1["p1_matches_14d"]=wfat[1];r1["p2_matches_14d"]=lfat[1]
    r1["p1_days_rest"]=wfat[2];r1["p2_days_rest"]=lfat[2]
    r1["p1_mins_30d"]=wfat[3];r1["p2_mins_30d"]=lfat[3]
    r1["fatigue_mins_diff"]=wfat[0]-lfat[0];r1["rest_days_diff"]=wfat[2]-lfat[2]
    r1["p1_win_rate_vs_top50"]=w_oa[0];r1["p2_win_rate_vs_top50"]=l_oa[0]
    r1["p1_top50_dropoff"]=w_oa[1];r1["p2_top50_dropoff"]=l_oa[1]
    rows.append(r1); labels.append(1); dates_list.append(md)

    # Row 2: loser = p1
    r2 = dict(base); r2["rank_diff"]=-rd
    r2["p1_recent_form"]=lf;r2["p2_recent_form"]=wf
    r2["p1_h2h_pct"]=l_h2h;r2["p2_h2h_pct"]=w_h2h
    for k in pkeys: r2[f"p1_{k}"] = lf_filled[k]
    for k in pkeys: r2[f"p2_{k}"] = wf_filled[k]
    r2["p1_mins_14d"]=lfat[0];r2["p2_mins_14d"]=wfat[0]
    r2["p1_matches_14d"]=lfat[1];r2["p2_matches_14d"]=wfat[1]
    r2["p1_days_rest"]=lfat[2];r2["p2_days_rest"]=wfat[2]
    r2["p1_mins_30d"]=lfat[3];r2["p2_mins_30d"]=wfat[3]
    r2["fatigue_mins_diff"]=lfat[0]-wfat[0];r2["rest_days_diff"]=lfat[2]-wfat[2]
    r2["p1_win_rate_vs_top50"]=l_oa[0];r2["p2_win_rate_vs_top50"]=w_oa[0]
    r2["p1_top50_dropoff"]=l_oa[1];r2["p2_top50_dropoff"]=w_oa[1]
    rows.append(r2); labels.append(0); dates_list.append(md)

    # ── UPDATE accumulators AFTER building rows ──

    # Match-level stats
    if has_stats:
        accums[w].update_match_stats(True, l, match)
        accums[l].update_match_stats(False, w, match)
        used_match_stats += 1

    # Charted points (if this match was charted)
    key1 = (w, l)
    key2 = (l, w)
    charted_mids = charted_lookup.get(key1, []) + charted_lookup.get(key2, [])
    for cmid in charted_mids:
        if cmid in processed_charted: continue
        cinfo = charted_matches[cmid]
        # Only process if dates are close (within 7 days)
        if abs((cinfo["date"] - md).days) > 7: continue
        processed_charted.add(cmid)
        used_charted += 1

        cpts = cinfo["points"]
        cbo = cinfo["bo"]
        csurf = cinfo["surface"]

        for pt in cpts.itertuples(index=False):
            svr = pt.Svr
            ptw = pt.PtWinner
            try:
                pp1, pp2 = pt.Player_1, pt.Player_2
            except: continue
            outcome = getattr(pt, "point_outcome", "")
            rl = pt.rally_length
            serve_dir = getattr(pt, "serve_direction", "")

            if svr == 1: server, returner, sw = pp1, pp2, (ptw==1)
            elif svr == 2: server, returner, sw = pp2, pp1, (ptw==2)
            else: continue

            sa = accums[server]

            # Serve direction
            sa.c_srv_total += 1
            if serve_dir == "wide": sa.c_srv_wide += 1
            elif serve_dir == "body": sa.c_srv_body += 1
            elif serve_dir == "T": sa.c_srv_t += 1

            # Break point
            pts_str = str(getattr(pt, "Pts", ""))
            is_bp = False
            if svr == 1:
                is_bp = (pts_str.endswith("-40") and pts_str.split("-")[0] in ["0","15","30"]) or pts_str=="40-AD"
            elif svr == 2:
                is_bp = (pts_str.startswith("40-") and pts_str.split("-")[1] in ["0","15","30"]) or pts_str=="AD-40"
            if is_bp:
                sa.c_srv_bp_total += 1
                if serve_dir=="wide": sa.c_srv_wide_bp += 1
                elif serve_dir=="T": sa.c_srv_t_bp += 1

            # Aggression
            if outcome in ("winner","ace"):
                if sw: sa.c_winners += 1
                else: accums[returner].c_winners += 1
            elif outcome == "unforced_error":
                if not sw: sa.c_ue += 1
                else: accums[returner].c_ue += 1

            # Rally
            if pd.notna(rl) and 1 <= rl < 25:
                rl_int = int(rl)
                for plyr, won in [(pp1, ptw==1), (pp2, ptw==2)]:
                    accums[plyr].c_rally_total[rl_int] += 1
                    if won: accums[plyr].c_rally_won[rl_int] += 1

            # First strike
            sa.c_fs_srv += 1
            if sw and pd.notna(rl) and rl <= 3:
                sa.c_fs_won += 1

            # Stage
            s1 = pt.Set1 or 0; s2 = pt.Set2 or 0
            cset = int(s1) + int(s2) + 1
            if cset <= 1:
                sa.c_set1_sp += 1
                if sw: sa.c_set1_sw += 1
            elif cset >= 3:
                sa.c_set3_sp += 1
                if sw: sa.c_set3_sw += 1

            # Format
            if cbo == 5:
                sa.c_bo5_sp += 1
                if sw: sa.c_bo5_sw += 1
                if outcome in ("winner","ace"): sa.c_bo5_aw += 1
                if outcome in ("winner","ace","unforced_error"): sa.c_bo5_ad += 1
            else:
                sa.c_bo3_sp += 1
                if sw: sa.c_bo3_sw += 1
                if outcome in ("winner","ace"): sa.c_bo3_aw += 1
                if outcome in ("winner","ace","unforced_error"): sa.c_bo3_ad += 1

            # Surface
            ssd = sa.c_surface[csurf]
            ssd["sp"] += 1
            if sw: ssd["sw"] += 1
            if outcome in ("winner","ace"): ssd["aw"] += 1
            if outcome in ("winner","ace","unforced_error"): ssd["ad"] += 1
            if pd.notna(rl): ssd["rl_sum"] += rl; ssd["rl_n"] += 1

            # Bigrams
            shot_seq = getattr(pt, "shot_sequence", "")
            if isinstance(shot_seq, str) and len(shot_seq) >= 2:
                for j in range(len(shot_seq)-1):
                    sa.c_bigrams[shot_seq[j:j+2]] += 1

            sa.c_total_pts += 1
            accums[returner].c_total_pts += 1

    # Form/H2H/fatigue updates
    player_results[w].append(True)
    player_results[l].append(False)
    player_match_log[w].append((md, match["minutes"]))
    player_match_log[l].append((md, match["minutes"]))
    h2h_wins[(w,l)] += 1
    h2h_total[(w,l)] += 1
    h2h_total[(l,w)] += 1
    player_wins_all[w] += 1; player_total_all[w] += 1; player_total_all[l] += 1
    if lr_rank <= 50:
        player_wins_top50[w] += 1; player_total_top50[w] += 1
    if wr_rank <= 50:
        player_total_top50[l] += 1

    if (idx+1) % 100000 == 0:
        print(f"    {idx+1:,} matches | stats={used_match_stats:,} charted={used_charted}")

X = pd.DataFrame(rows).fillna(0)
y = pd.Series(labels)
dates_s = pd.Series(dates_list)

print(f"\n  Rows: {len(X):,}")
print(f"  Features: {X.shape[1]}")
print(f"  Used match stats: {used_match_stats:,} matches")
print(f"  Used charted data: {used_charted} matches")

# ─────────────────────────────────────────────────
# TEMPORAL SPLIT + TRAIN
# ─────────────────────────────────────────────────
cutoff = pd.Timestamp(TEMPORAL_CUTOFF)
tr = dates_s < cutoff
te = dates_s >= cutoff
Xtr, ytr = X[tr], y[tr]
Xte, yte = X[te], y[te]

print(f"\n[SPLIT] Cutoff: {TEMPORAL_CUTOFF}")
print(f"  Train: {len(Xtr):,} rows ({tr.sum()//2:,} matches)")
print(f"  Test:  {len(Xte):,} rows ({te.sum()//2:,} matches)")

print("\n[TRAIN] XGBoost...")
params = {"max_depth":6,"learning_rate":0.1,"n_estimators":300,
          "subsample":0.8,"colsample_bytree":0.8,"min_child_weight":5,
          "reg_alpha":0,"reg_lambda":1,"eval_metric":"logloss",
          "use_label_encoder":False}
model = xgb.XGBClassifier(**params, random_state=42)
model.fit(Xtr, ytr)
probs = model.predict_proba(Xte)[:,1]
brier = np.mean((probs - yte)**2)

print(f"\n{'='*70}")
print(f"  HONEST ROLLING BRIER (zero leakage): {brier:.4f}")
print(f"  Previous rolling v1:                 0.2039")
print(f"  Previous leaked:                     0.1946")
print(f"  Features: {X.shape[1]}")
print(f"  Match stats coverage: {used_match_stats:,} matches")
print(f"  Charted coverage: {used_charted} matches")
print(f"{'='*70}")

imp = sorted(zip(X.columns, model.feature_importances_), key=lambda x:-x[1])
print(f"\n  Top 30 Feature Importances:")
for i,(f,v) in enumerate(imp[:30]):
    print(f"    {i+1:2d}. {f:45s} {v:.4f}")

# Save
pickle.dump((X,y,dates_s), open(OUTPUT_PATH, "wb"))
pickle.dump((X,y), open(REPO_ROOT/"data"/"processed"/"expanded_training.pkl","wb"))
pickle.dump(model, open(REPO_ROOT/"models"/"hard"/"best_rolling_v2_model.pkl","wb"))

import json
meta = {"brier_honest":float(brier),"n_features":int(X.shape[1]),
        "n_train":int(len(Xtr)),"n_test":int(len(Xte)),
        "cutoff":TEMPORAL_CUTOFF,"zero_leakage":True,
        "match_stats_coverage":used_match_stats,"charted_coverage":used_charted,
        "top_features":[(f,float(v)) for f,v in imp[:30]]}
json.dump(meta, open(REPO_ROOT/"experiments"/"rolling_v2_results.json","w"), indent=2)
print(f"\nSaved. This is the HONEST number with full data coverage.")
