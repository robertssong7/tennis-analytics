"""
TennisIQ Deep Player Analysis v2
Full permutation analysis: score-state, momentum, serve+1, return patterns,
error types, surface splits, format splits, court side, opponent exploitation.
Usage: python3 scripts/deep_player_analysis.py "Carlos Alcaraz"
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path
from scipy import stats
from collections import Counter

REPO_ROOT = Path(__file__).resolve().parent.parent
PARSED_POINTS = REPO_ROOT / "data" / "processed" / "parsed_points.parquet"


def fuzzy_find(name, known):
    nl = name.strip().lower()
    for p in known:
        if p.lower() == nl:
            return p
    matches = [p for p in known if nl in p.lower()]
    if len(matches) == 1:
        return matches[0]
    matches = [p for p in known if nl.split()[-1].lower() in p.lower()]
    if len(matches) == 1:
        return matches[0]
    return None


def pct(v):
    return f"{v*100:.1f}%"


SCORE_TO_PTS = {
    "0-0":0,"15-0":1,"0-15":1,"30-0":2,"15-15":2,"0-30":2,
    "40-0":3,"30-15":3,"15-30":3,"0-40":3,"40-15":4,"30-30":4,
    "15-40":4,"40-30":5,"30-40":5,"40-40":6,"AD-40":7,"40-AD":7,
}


def enrich(df, player):
    df = df.copy()
    df["is_p1"] = df["Player 1"] == player
    df["is_serving"] = (
        ((df["Svr"] == 1) & df["is_p1"]) |
        ((df["Svr"] == 2) & ~df["is_p1"])
    )
    df["won_point"] = (
        ((df["PtWinner"] == 1) & df["is_p1"]) |
        ((df["PtWinner"] == 2) & ~df["is_p1"])
    )
    # Game/set lead from player perspective
    p_gm = np.where(df["is_p1"], df["Gm1"], df["Gm2"]).astype(float)
    o_gm = np.where(df["is_p1"], df["Gm2"], df["Gm1"]).astype(float)
    df["game_lead"] = p_gm - o_gm
    p_st = np.where(df["is_p1"], df["Set1"], df["Set2"]).astype(float)
    o_st = np.where(df["is_p1"], df["Set2"], df["Set1"]).astype(float)
    df["set_lead"] = p_st - o_st
    # Court side
    pts_played = df["Pts"].map(SCORE_TO_PTS)
    df["court_side"] = np.where(pts_played % 2 == 0, "deuce", "ad")
    df.loc[pts_played.isna(), "court_side"] = "unknown"
    # Tiebreak
    df["is_tiebreak"] = (df["Gm1"].fillna(0) >= 6) & (df["Gm2"].fillna(0) >= 6)
    # Game number in set
    df["game_in_set"] = np.nan_to_num(p_gm + o_gm, nan=0).astype(int)
    # First serve vs second serve
    df["is_2nd_serve"] = df["2nd"].notna() & (df["2nd"].astype(str).str.strip() != "")
    # Break point facing
    pts_str = df["Pts"].astype(str)
    svr = df["Svr"]
    df["is_bp_facing"] = False
    bp1 = df["is_serving"] & (svr==1) & (
        (pts_str.str.endswith("-40") & pts_str.str.split("-").str[0].isin(["0","15","30"])) | (pts_str=="40-AD"))
    bp2 = df["is_serving"] & (svr==2) & (
        (pts_str.str.startswith("40-") & pts_str.str.split("-").str[1].isin(["0","15","30"])) | (pts_str=="AD-40"))
    df.loc[bp1|bp2, "is_bp_facing"] = True
    # Game point holding
    df["is_gp_holding"] = False
    gp1 = df["is_serving"] & (svr==1) & (
        (pts_str.str.startswith("40-") & pts_str.str.split("-").str[1].isin(["0","15","30"])) | (pts_str=="AD-40"))
    gp2 = df["is_serving"] & (svr==2) & (
        (pts_str.str.endswith("-40") & pts_str.str.split("-").str[0].isin(["0","15","30"])) | (pts_str=="40-AD"))
    df.loc[gp1|gp2, "is_gp_holding"] = True
    # Previous point result (within same match)
    df["prev_won"] = df.groupby("match_id")["won_point"].shift(1)
    return df


def srv_stats(pts):
    n = len(pts)
    if n < 10:
        return None
    vc = pts["serve_direction"].value_counts()
    return {"n":n, "wide":vc.get("wide",0)/n, "body":vc.get("body",0)/n,
            "T":vc.get("T",0)/n, "win_rate":pts["won_point"].mean()}


def agg_stats(pts):
    n = len(pts)
    if n < 10:
        return None
    oc = pts["point_outcome"].value_counts()
    w = oc.get("winner",0)+oc.get("ace",0)
    ue = oc.get("unforced_error",0)
    fe = oc.get("forced_error",0)
    denom = w + ue
    return {"n":n, "aggression": w/denom if denom>10 else None,
            "winner_rate":w/n, "ue_rate":ue/n, "fe_rate":fe/n,
            "avg_rally":pts["rally_length"].mean(), "win_rate":pts["won_point"].mean()}


def error_stats(pts):
    """Breakdown of error types: net vs wide vs deep using last char of rally string."""
    n = len(pts)
    if n < 20:
        return None
    errors = pts[pts["point_outcome"].isin(["unforced_error", "forced_error"])]
    if len(errors) < 10:
        return None
    rally_strs = errors["1st"].fillna(errors["2nd"]).dropna().astype(str)
    last_char = rally_strs.str[-1]
    net = (last_char == "n").sum()
    wide = (last_char == "w").sum()
    deep = (last_char == "d").sum()
    total_err = net + wide + deep
    if total_err < 10:
        return None
    return {"n_errors":total_err, "net_pct":net/total_err, "wide_pct":wide/total_err,
            "deep_pct":deep/total_err}


def serve_plus1(pts):
    """Analyze the shot after serve (serve+1 pattern)."""
    serving = pts[pts["is_serving"]]
    n = len(serving)
    if n < 20:
        return None
    seqs = serving["shot_sequence"].dropna().astype(str)
    # shot_sequence format: SFBF* etc. Index 1 is the return, index 2 is serve+1
    plus1 = []
    for s in seqs:
        if len(s) >= 3:
            plus1.append(s[2])
    if len(plus1) < 15:
        return None
    counts = Counter(plus1)
    total = sum(counts.values())
    top = counts.most_common(5)
    ABBREVS = {"F":"Forehand","B":"Backhand","s":"Slice","V":"FH Volley",
               "Z":"BH Volley","r":"FH Slice","O":"Overhead","H":"FH Drop","J":"BH Drop"}
    return [{"shot":ABBREVS.get(s,s), "pct":c/total, "count":c} for s,c in top]


def compare_split(w_pts, l_pts, label, min_n=15):
    """Compare wins vs losses for a situation. Return list of insights."""
    insights = []
    ws = srv_stats(w_pts[w_pts["is_serving"]])
    ls = srv_stats(l_pts[l_pts["is_serving"]])

    if ws and ls and ws["n"] >= min_n and ls["n"] >= min_n:
        for d in ["wide","body","T"]:
            delta = ws[d] - ls[d]
            if abs(delta) > 0.035:
                insights.append({"situation":label, "type":"serve_dir", "detail":f"Serve {d}",
                    "in_wins":ws[d], "in_losses":ls[d], "delta":delta,
                    "insight":f"At {label}: serve {d} {pct(ws[d])} in wins vs {pct(ls[d])} in losses ({'+' if delta>0 else ''}{pct(delta)})"})
        wr_d = ws["win_rate"] - ls["win_rate"]
        if abs(wr_d) > 0.05:
            insights.append({"situation":label, "type":"serve_eff", "detail":"Serve pt win%",
                "in_wins":ws["win_rate"], "in_losses":ls["win_rate"], "delta":wr_d,
                "insight":f"At {label}: wins {pct(ws['win_rate'])} serve pts in match wins vs {pct(ls['win_rate'])} in losses"})

    wa = agg_stats(w_pts)
    la = agg_stats(l_pts)
    if wa and la and wa["aggression"] and la["aggression"]:
        ad = wa["aggression"] - la["aggression"]
        if abs(ad) > 0.035:
            insights.append({"situation":label, "type":"aggression", "detail":"Aggression",
                "in_wins":wa["aggression"], "in_losses":la["aggression"], "delta":ad,
                "insight":f"At {label}: aggression {pct(wa['aggression'])} in wins vs {pct(la['aggression'])} in losses"})
        rd = wa["avg_rally"] - la["avg_rally"]
        if abs(rd) > 0.4:
            insights.append({"situation":label, "type":"rally_len", "detail":"Avg rally",
                "in_wins":wa["avg_rally"], "in_losses":la["avg_rally"], "delta":rd,
                "insight":f"At {label}: rally {wa['avg_rally']:.1f} shots in wins vs {la['avg_rally']:.1f} in losses"})

    return insights


def full_analysis(points, player):
    mask = (points["Player 1"]==player) | (points["Player 2"]==player)
    pts = enrich(points[mask], player)

    # Match results
    mr = {}
    for mid, mdf in pts.groupby("match_id"):
        is_p1 = mdf.iloc[0]["Player 1"] == player
        p1w = (mdf["PtWinner"]==1).sum()
        p2w = (mdf["PtWinner"]==2).sum()
        mr[mid] = (is_p1 and p1w>p2w) or (not is_p1 and p2w>p1w)

    pts["won_match"] = pts["match_id"].map(mr)
    W = pts[pts["won_match"]]
    L = pts[~pts["won_match"]]
    n_w = sum(mr.values())
    n_l = len(mr) - n_w

    all_ins = []

    # ═══ 1. SCORE-STATE SITUATIONS ═══
    score_sits = [
        ("0-0 (opening)", lambda d: d["is_serving"] & (d["Pts"].astype(str)=="0-0")),
        ("0-30 (trouble)", lambda d: d["is_serving"] & (
            ((d["Svr"]==1)&(d["Pts"].astype(str)=="0-30"))|((d["Svr"]==2)&(d["Pts"].astype(str)=="30-0")))),
        ("0-40 (triple BP)", lambda d: d["is_serving"] & (
            ((d["Svr"]==1)&(d["Pts"].astype(str)=="0-40"))|((d["Svr"]==2)&(d["Pts"].astype(str)=="40-0")))),
        ("15-40 (double BP)", lambda d: d["is_serving"] & (
            ((d["Svr"]==1)&(d["Pts"].astype(str)=="15-40"))|((d["Svr"]==2)&(d["Pts"].astype(str)=="40-15")))),
        ("30-40 (break pt)", lambda d: d["is_serving"] & (
            ((d["Svr"]==1)&(d["Pts"].astype(str)=="30-40"))|((d["Svr"]==2)&(d["Pts"].astype(str)=="40-30")))),
        ("40-40 (deuce)", lambda d: d["is_serving"] & (d["Pts"].astype(str)=="40-40")),
        ("Break point (any)", lambda d: d["is_bp_facing"]),
        ("Game point (hold)", lambda d: d["is_gp_holding"]),
    ]
    for lab, fn in score_sits:
        try:
            ws, ls = W[fn(W)], L[fn(L)]
            if len(ws)>=15 and len(ls)>=15:
                all_ins.extend(compare_split(ws, ls, lab))
        except: pass

    # ═══ 2. GAME CONTEXT ═══
    for lab, fn in [
        ("Down 2+ breaks", lambda d: d["game_lead"]<=-2),
        ("Down 1 break", lambda d: (d["game_lead"]>=-1.5)&(d["game_lead"]<0)),
        ("On serve", lambda d: d["game_lead"]==0),
        ("Up 1 break", lambda d: (d["game_lead"]>0)&(d["game_lead"]<=1.5)),
        ("Up 2+ breaks", lambda d: d["game_lead"]>=2),
    ]:
        try:
            ws, ls = W[fn(W)], L[fn(L)]
            if len(ws)>=30 and len(ls)>=30:
                all_ins.extend(compare_split(ws, ls, lab))
        except: pass

    # ═══ 3. SET CONTEXT ═══
    for lab, fn in [
        ("Down a set", lambda d: d["set_lead"]<0),
        ("Sets level", lambda d: d["set_lead"]==0),
        ("Up a set", lambda d: d["set_lead"]>0),
    ]:
        try:
            ws, ls = W[fn(W)], L[fn(L)]
            if len(ws)>=30 and len(ls)>=30:
                all_ins.extend(compare_split(ws, ls, lab))
        except: pass

    # ═══ 4. COURT SIDE ═══
    for lab, fn in [
        ("Deuce court", lambda d: d["is_serving"] & (d["court_side"]=="deuce")),
        ("Ad court", lambda d: d["is_serving"] & (d["court_side"]=="ad")),
    ]:
        try:
            ws, ls = W[fn(W)], L[fn(L)]
            if len(ws)>=30 and len(ls)>=30:
                all_ins.extend(compare_split(ws, ls, lab))
        except: pass

    # ═══ 5. FIRST SERVE vs SECOND SERVE ═══
    for lab, fn in [
        ("1st serve", lambda d: d["is_serving"] & ~d["is_2nd_serve"]),
        ("2nd serve", lambda d: d["is_serving"] & d["is_2nd_serve"]),
    ]:
        try:
            ws, ls = W[fn(W)], L[fn(L)]
            if len(ws)>=20 and len(ls)>=20:
                all_ins.extend(compare_split(ws, ls, lab))
        except: pass

    # ═══ 6. MOMENTUM — after winning/losing previous point ═══
    for lab, fn in [
        ("After winning prev pt", lambda d: d["is_serving"] & (d["prev_won"]==True)),
        ("After losing prev pt", lambda d: d["is_serving"] & (d["prev_won"]==False)),
    ]:
        try:
            ws, ls = W[fn(W)], L[fn(L)]
            if len(ws)>=30 and len(ls)>=30:
                all_ins.extend(compare_split(ws, ls, lab))
        except: pass

    # ═══ 7. EARLY vs LATE IN SET ═══
    for lab, fn in [
        ("Early set (games 0-3)", lambda d: d["game_in_set"]<=3),
        ("Mid set (games 4-7)", lambda d: (d["game_in_set"]>=4)&(d["game_in_set"]<=7)),
        ("Late set (games 8+)", lambda d: d["game_in_set"]>=8),
    ]:
        try:
            ws, ls = W[fn(W)], L[fn(L)]
            if len(ws)>=30 and len(ls)>=30:
                all_ins.extend(compare_split(ws, ls, lab))
        except: pass

    # ═══ 8. TIEBREAK ═══
    try:
        ws = W[W["is_tiebreak"]]
        ls = L[L["is_tiebreak"]]
        if len(ws)>=15 and len(ls)>=15:
            all_ins.extend(compare_split(ws, ls, "Tiebreak"))
    except: pass

    # ═══ 9. BEST-OF-3 vs BEST-OF-5 ═══
    for lab, fn in [
        ("Best-of-3", lambda d: d["Best of"].astype(str).str.strip()=="3"),
        ("Best-of-5", lambda d: d["Best of"].astype(str).str.strip()=="5"),
    ]:
        try:
            ws, ls = W[fn(W)], L[fn(L)]
            if len(ws)>=50 and len(ls)>=50:
                all_ins.extend(compare_split(ws, ls, lab))
        except: pass

    # ═══ 10. SURFACE-SPECIFIC ═══
    for surface in ["Hard", "Clay", "Grass"]:
        try:
            ws = W[W["Surface"]==surface]
            ls = L[L["Surface"]==surface]
            if len(ws)>=50 and len(ls)>=50:
                all_ins.extend(compare_split(ws, ls, f"On {surface}"))
        except: pass

    # ═══ 11. ERROR TYPE ANALYSIS ═══
    err_insights = []
    we = error_stats(W)
    le = error_stats(L)
    if we and le:
        for etype, elabel in [("net_pct","Net errors"),("wide_pct","Wide errors"),("deep_pct","Deep errors")]:
            d = we[etype] - le[etype]
            if abs(d) > 0.03:
                err_insights.append({
                    "type":"error_type", "detail":elabel,
                    "in_wins":we[etype], "in_losses":le[etype], "delta":d,
                    "insight":f"{elabel}: {pct(we[etype])} of errors in wins vs {pct(le[etype])} in losses — {'pressing' if etype=='deep_pct' and d>0 else 'tightening' if etype=='net_pct' and d>0 else 'shifting'}"
                })

    # ═══ 12. SERVE+1 ANALYSIS ═══
    sp1_w = serve_plus1(W)
    sp1_l = serve_plus1(L)

    # ═══ 13. OPPONENT EXPLOITATION ═══
    opp_ins = []
    opp_sv_w = W[~W["is_serving"]]
    opp_sv_l = L[~L["is_serving"]]
    if len(opp_sv_w)>50 and len(opp_sv_l)>50:
        for d in ["wide","body","T"]:
            wp = (opp_sv_w["serve_direction"]==d).mean()
            lp = (opp_sv_l["serve_direction"]==d).mean()
            delta = lp - wp
            if abs(delta)>0.015:
                opp_ins.append({"type":"opp_serve","detail":f"Opp serve {d}",
                    "when_wins":wp,"when_loses":lp,"delta":delta,
                    "insight":f"Opponents serve {d} {pct(lp)} when they beat {player.split()[-1]} vs {pct(wp)} when they lose"})
        # Opponent rally length
        wrl = opp_sv_w["rally_length"].mean()
        lrl = opp_sv_l["rally_length"].mean()
        if abs(lrl-wrl) > 0.2:
            opp_ins.append({"type":"opp_rally","detail":"Opp rally length",
                "when_wins":wrl,"when_loses":lrl,"delta":lrl-wrl,
                "insight":f"Opponents extend rallies to {lrl:.1f} shots when beating {player.split()[-1]} vs {wrl:.1f} when losing"})
        # Opponent aggression
        w_oc = opp_sv_w["point_outcome"].value_counts()
        l_oc = opp_sv_l["point_outcome"].value_counts()
        wa2 = (w_oc.get("winner",0)+w_oc.get("ace",0))
        wa_d = wa2+w_oc.get("unforced_error",0)
        la2 = (l_oc.get("winner",0)+l_oc.get("ace",0))
        la_d = la2+l_oc.get("unforced_error",0)
        if wa_d>20 and la_d>20:
            wa_agg = wa2/wa_d
            la_agg = la2/la_d
            if abs(la_agg-wa_agg) > 0.015:
                opp_ins.append({"type":"opp_aggression","detail":"Opp aggression",
                    "when_wins":wa_agg,"when_loses":la_agg,"delta":la_agg-wa_agg,
                    "insight":f"Opponents are {'more' if la_agg>wa_agg else 'less'} aggressive when they beat {player.split()[-1]} ({pct(la_agg)} vs {pct(wa_agg)})"})

    # ═══ 14. RETURN PATTERNS ═══
    ret_ins = []
    ret_w = W[~W["is_serving"]]
    ret_l = L[~L["is_serving"]]
    if len(ret_w)>50 and len(ret_l)>50:
        w_agg = agg_stats(ret_w)
        l_agg = agg_stats(ret_l)
        if w_agg and l_agg and w_agg["aggression"] and l_agg["aggression"]:
            d = w_agg["aggression"]-l_agg["aggression"]
            if abs(d)>0.03:
                ret_ins.append({"detail":"Return aggression","in_wins":w_agg["aggression"],
                    "in_losses":l_agg["aggression"],"delta":d,
                    "insight":f"Return aggression: {pct(w_agg['aggression'])} in wins vs {pct(l_agg['aggression'])} in losses"})
            rd = w_agg["avg_rally"]-l_agg["avg_rally"]
            if abs(rd)>0.3:
                ret_ins.append({"detail":"Return rally length","in_wins":w_agg["avg_rally"],
                    "in_losses":l_agg["avg_rally"],"delta":rd,
                    "insight":f"Return rallies: {w_agg['avg_rally']:.1f} shots in wins vs {l_agg['avg_rally']:.1f} in losses"})
        # Return win rate
        w_rw = ret_w["won_point"].mean()
        l_rw = ret_l["won_point"].mean()
        if abs(w_rw-l_rw) > 0.05:
            ret_ins.append({"detail":"Return pt win%","in_wins":w_rw,"in_losses":l_rw,
                "delta":w_rw-l_rw,
                "insight":f"Return points won: {pct(w_rw)} in wins vs {pct(l_rw)} in losses"})

    all_ins.sort(key=lambda x: abs(x["delta"]), reverse=True)
    opp_ins.sort(key=lambda x: abs(x["delta"]), reverse=True)

    return {
        "player":player, "wins":n_w, "losses":n_l, "total_pts":len(pts),
        "situational_insights": all_ins,
        "opponent_exploits": opp_ins,
        "error_analysis": err_insights,
        "serve_plus1_wins": sp1_w,
        "serve_plus1_losses": sp1_l,
        "return_insights": ret_ins,
    }


def print_report(r):
    last = r["player"].split()[-1]
    print("=" * 82)
    print(f"  TENNISIQ DEEP ANALYSIS: {r['player']}")
    print(f"  {r['wins']}W - {r['losses']}L | {r['total_pts']} points")
    print("=" * 82)

    si = r["situational_insights"]
    if si:
        print(f"\n  ── SITUATIONAL PATTERNS: WINS vs LOSSES ({len(si)} significant) ──")
        by_sit = {}
        for i in si:
            by_sit.setdefault(i["situation"],[]).append(i)
        for sit, items in by_sit.items():
            print(f"\n  {sit}:")
            for i in items:
                w = pct(i["in_wins"]) if isinstance(i["in_wins"],float) and i["in_wins"]<=1.0 else f"{i['in_wins']:.1f}" if isinstance(i["in_wins"],float) else str(i["in_wins"])
                l = pct(i["in_losses"]) if isinstance(i["in_losses"],float) and i["in_losses"]<=1.0 else f"{i['in_losses']:.1f}" if isinstance(i["in_losses"],float) else str(i["in_losses"])
                d = i["delta"]
                print(f"    {i['detail']:<26s} W: {w:>7s}  L: {l:>7s}  ({'+' if d>0 else ''}{pct(d) if abs(d)<10 else f'{d:.1f}'})")

    ri = r["return_insights"]
    if ri:
        print(f"\n  ── RETURN GAME ({len(ri)} patterns) ──")
        for i in ri:
            print(f"    {i['insight']}")

    ei = r["error_analysis"]
    if ei:
        print(f"\n  ── ERROR TYPE SHIFTS ({len(ei)} patterns) ──")
        for i in ei:
            print(f"    {i['insight']}")

    sp1w = r["serve_plus1_wins"]
    sp1l = r["serve_plus1_losses"]
    if sp1w and sp1l:
        print(f"\n  ── SERVE+1 SHOT (first shot after serve) ──")
        print(f"    {'Shot':<16s} {'In Wins':>10s} {'In Losses':>10s}")
        for w, l in zip(sp1w[:5], sp1l[:5]):
            print(f"    {w['shot']:<16s} {pct(w['pct']):>10s} {pct(l['pct']):>10s}")

    oi = r["opponent_exploits"]
    if oi:
        print(f"\n  ── HOW OPPONENTS BEAT {last.upper()} ({len(oi)} patterns) ──")
        for i in oi:
            print(f"    {i['insight']}")

    print(f"\n{'=' * 82}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/deep_player_analysis.py 'Player Name'")
        sys.exit(0)
    print("Loading data...")
    points = pd.read_parquet(PARSED_POINTS)
    all_names = sorted(set(points["Player 1"].unique()) | set(points["Player 2"].unique()))
    name = fuzzy_find(sys.argv[1], all_names)
    if not name:
        print(f"Player '{sys.argv[1]}' not found.")
        sys.exit(1)
    print(f"Analyzing {name}...")
    result = full_analysis(points, name)
    print_report(result)


if __name__ == "__main__":
    main()
