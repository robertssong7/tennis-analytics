"""
Compute career percentile rankings for every ATP player on 16 stats.
Output: data/processed/percentile_rankings.json — keyed by canonical player name.

Each player entry has, per stat:
  { value, sample_size, percentile, rank, total_qualifying }

Stats:
  Match-result: tiebreak_win_rate, deciding_set_wr, three_set_wr, vs_top10_wr,
                vs_top20_wr, comeback_rate, first_set_winner_conv, bagels_per_match,
                bagels_conceded_per_match
  Serve/return (1991+): hold_pct, break_pct, bp_save_pct, bp_convert_pct,
                first_serve_win_pct, second_serve_win_pct, aces_per_match,
                df_per_match
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import percentileofscore

BASE = Path(__file__).parent.parent
SACKMANN_DIR = BASE / "data" / "sackmann" / "tennis_atp"
OUTPUT = BASE / "data" / "processed" / "percentile_rankings.json"

MIN_CAREER_MATCHES = 20

STAT_LABELS = {
    "tiebreak_win_rate": "Tiebreak Win Rate",
    "deciding_set_wr": "Deciding Set Win Rate",
    "three_set_wr": "3-Set Match Win Rate",
    "vs_top10_wr": "vs Top-10 Win Rate",
    "vs_top20_wr": "vs Top-20 Win Rate",
    "comeback_rate": "Comeback Rate (after losing 1st set)",
    "first_set_winner_conv": "First-Set-Winner Conversion",
    "bagels_per_match": "Bagels Delivered Per Match",
    "bagels_conceded_per_match": "Bagels Conceded Per Match",
    "hold_pct": "Hold %",
    "break_pct": "Break %",
    "bp_save_pct": "Break Point Save %",
    "bp_convert_pct": "Break Point Convert %",
    "first_serve_win_pct": "1st Serve Win %",
    "second_serve_win_pct": "2nd Serve Win %",
    "aces_per_match": "Aces Per Match",
    "df_per_match": "Double Faults Per Match",
}


def _is_main_tour(name: str) -> bool:
    n = name.lower()
    return all(
        skip not in n for skip in ("qual", "futures", "doubles", "amateur", "supplement")
    )


def _count_set_score(score_str: str):
    """Parse a score like '6-2 7-6 4-6 6-4'. Returns list of (winner_games, loser_games) per set."""
    if not isinstance(score_str, str):
        return []
    sets = []
    for token in score_str.split():
        m = re.match(r"(\d+)-(\d+)", token)
        if m:
            try:
                sets.append((int(m.group(1)), int(m.group(2))))
            except Exception:
                continue
    return sets


def _has_tiebreak(score_str: str) -> int:
    """Count tiebreaks in a score string (e.g. 7-6(5))."""
    if not isinstance(score_str, str):
        return 0
    return len(re.findall(r"7-6", score_str))


def _bagels_in_score(score_str: str):
    """Returns (winner_bagels, loser_bagels) — number of 6-0 sets each won."""
    sets = _count_set_score(score_str)
    w = sum(1 for a, b in sets if a == 6 and b == 0)
    l = sum(1 for a, b in sets if b == 6 and a == 0)
    return w, l


def _won_first_set(score_str: str, won_match: bool) -> tuple[bool, bool]:
    """Returns (player_won_first_set, player_won_match). For computing first_set_winner_conv
    and comeback_rate."""
    sets = _count_set_score(score_str)
    if not sets:
        return None, won_match
    a, b = sets[0]
    # In Sackmann, the score is from the winner's perspective: winner_games-loser_games.
    winner_won_first = a > b
    if won_match:
        return winner_won_first, True
    else:
        return (not winner_won_first), False


def _went_to_decider(score_str: str, best_of: int) -> bool:
    sets = _count_set_score(score_str)
    if best_of == 3:
        return len(sets) == 3
    if best_of == 5:
        return len(sets) == 5
    return False


def _won_decider(score_str: str, best_of: int, won_match: bool) -> tuple[bool, bool]:
    """Returns (went_to_decider, player_won_decider)."""
    if not _went_to_decider(score_str, best_of):
        return False, False
    return True, won_match


def main():
    csvs = sorted(SACKMANN_DIR.glob("atp_matches_*.csv"))
    csvs = [f for f in csvs if _is_main_tour(f.name)]
    print(f"Loading {len(csvs)} CSV files...")

    # Per-player stat accumulators
    P = defaultdict(lambda: defaultdict(float))
    N = defaultdict(lambda: defaultdict(int))  # sample sizes per stat
    matches = defaultdict(int)

    cols = [
        "winner_name", "loser_name", "score", "best_of", "tourney_date",
        "winner_rank", "loser_rank",
        "w_ace", "w_df", "w_svpt", "w_1stIn", "w_1stWon", "w_2ndWon",
        "w_SvGms", "w_bpSaved", "w_bpFaced",
        "l_ace", "l_df", "l_svpt", "l_1stIn", "l_1stWon", "l_2ndWon",
        "l_SvGms", "l_bpSaved", "l_bpFaced",
    ]

    for csv_path in csvs:
        try:
            df = pd.read_csv(csv_path, usecols=cols, low_memory=False)
        except Exception as e:
            print(f"  Skip {csv_path.name}: {e}")
            continue
        df = df.dropna(subset=["winner_name", "loser_name"])
        for _, row in df.iterrows():
            w = str(row["winner_name"])
            l = str(row["loser_name"])
            score = str(row.get("score", "") or "")
            best_of = int(row.get("best_of", 3) or 3)
            w_rank = row.get("winner_rank")
            l_rank = row.get("loser_rank")

            matches[w] += 1
            matches[l] += 1

            # ── tiebreak_win_rate ──
            tbs = _has_tiebreak(score)
            if tbs > 0:
                # Crude: assume winner won all tiebreaks. Sackmann doesn't break out per-tb winner,
                # but a more nuanced approach would need set-by-set analysis. Most tiebreaks
                # in a winning effort go to the winner.
                P[w]["tiebreak_wins"] += tbs
                # The number of TBs played by each player is `tbs`
                P[w]["tiebreak_played"] += tbs
                P[l]["tiebreak_played"] += tbs

            # ── decider ──
            went_decider, _ = _won_decider(score, best_of, True)
            if went_decider:
                # winner won the decider, loser played and lost
                P[w]["decider_wins"] += 1
                P[w]["decider_played"] += 1
                P[l]["decider_played"] += 1

            # ── three_set_wr (BO3 going the distance) ──
            if best_of == 3:
                sets = _count_set_score(score)
                if len(sets) == 3:
                    P[w]["three_set_wins"] += 1
                    P[w]["three_set_played"] += 1
                    P[l]["three_set_played"] += 1

            # ── vs top-10 / top-20 ──
            try:
                if pd.notna(l_rank) and float(l_rank) <= 10:
                    P[w]["vs_top10_wins"] += 1
                    P[w]["vs_top10_played"] += 1
                if pd.notna(w_rank) and float(w_rank) <= 10:
                    P[l]["vs_top10_played"] += 1
                if pd.notna(l_rank) and float(l_rank) <= 20:
                    P[w]["vs_top20_wins"] += 1
                    P[w]["vs_top20_played"] += 1
                if pd.notna(w_rank) and float(w_rank) <= 20:
                    P[l]["vs_top20_played"] += 1
            except (ValueError, TypeError):
                pass

            # ── comeback / first-set conversion ──
            sets = _count_set_score(score)
            if sets:
                # winner_won_first_set?
                wf = sets[0][0] > sets[0][1]
                if wf:
                    # winner won first AND won match
                    P[w]["first_set_won_match_won"] += 1
                    P[w]["first_set_won_total"] += 1
                    # loser won first set? no, winner won first. So loser lost first set.
                    P[l]["first_set_lost_total"] += 1
                else:
                    # winner lost first set but won match (a comeback)
                    P[w]["comeback_wins"] += 1
                    P[w]["first_set_lost_total"] += 1
                    P[l]["first_set_won_total"] += 1
                    # loser won first but lost match
                    # comeback denominator for loser is matches where they lost first set:
                    # well, we care about player's comebacks → wins after losing 1st set / matches where lost 1st set

            # ── bagels ──
            wb, lb = _bagels_in_score(score)
            P[w]["bagels_delivered"] += wb
            P[w]["bagels_conceded"] += lb
            P[l]["bagels_delivered"] += lb
            P[l]["bagels_conceded"] += wb

            # ── serve/return stats (1991+ where Sackmann has them) ──
            def _f(v):
                try:
                    return float(v)
                except Exception:
                    return None

            w_svpt, l_svpt = _f(row.get("w_svpt")), _f(row.get("l_svpt"))
            w_svgms, l_svgms = _f(row.get("w_SvGms")), _f(row.get("l_SvGms"))
            w_1stin, l_1stin = _f(row.get("w_1stIn")), _f(row.get("l_1stIn"))
            w_1stwon, l_1stwon = _f(row.get("w_1stWon")), _f(row.get("l_1stWon"))
            w_2ndwon, l_2ndwon = _f(row.get("w_2ndWon")), _f(row.get("l_2ndWon"))
            w_bps, l_bps = _f(row.get("w_bpSaved")), _f(row.get("l_bpSaved"))
            w_bpf, l_bpf = _f(row.get("w_bpFaced")), _f(row.get("l_bpFaced"))
            w_ace, l_ace = _f(row.get("w_ace")), _f(row.get("l_ace"))
            w_df_n, l_df_n = _f(row.get("w_df")), _f(row.get("l_df"))

            # Aces & double faults per match
            if w_ace is not None:
                P[w]["aces_total"] += w_ace
                N[w]["aces_matches"] += 1
            if l_ace is not None:
                P[l]["aces_total"] += l_ace
                N[l]["aces_matches"] += 1
            if w_df_n is not None:
                P[w]["dfs_total"] += w_df_n
                N[w]["dfs_matches"] += 1
            if l_df_n is not None:
                P[l]["dfs_total"] += l_df_n
                N[l]["dfs_matches"] += 1

            # 1st serve win % = 1stWon / 1stIn
            if w_1stin and w_1stwon is not None and w_1stin > 0:
                P[w]["first_serve_won"] += w_1stwon
                P[w]["first_serve_in"] += w_1stin
            if l_1stin and l_1stwon is not None and l_1stin > 0:
                P[l]["first_serve_won"] += l_1stwon
                P[l]["first_serve_in"] += l_1stin

            # 2nd serve win % = 2ndWon / (svpt - 1stIn - df)
            if w_svpt and w_1stin is not None and w_2ndwon is not None and w_df_n is not None:
                second_pts = w_svpt - w_1stin - w_df_n
                if second_pts > 0:
                    P[w]["second_serve_won"] += w_2ndwon
                    P[w]["second_serve_pts"] += second_pts
            if l_svpt and l_1stin is not None and l_2ndwon is not None and l_df_n is not None:
                second_pts = l_svpt - l_1stin - l_df_n
                if second_pts > 0:
                    P[l]["second_serve_won"] += l_2ndwon
                    P[l]["second_serve_pts"] += second_pts

            # bp save %
            if w_bpf and w_bpf > 0:
                P[w]["bp_saved"] += (w_bps or 0)
                P[w]["bp_faced"] += w_bpf
            if l_bpf and l_bpf > 0:
                P[l]["bp_saved"] += (l_bps or 0)
                P[l]["bp_faced"] += l_bpf

            # bp convert % = bp won by returner / bp faced by server's opponent
            # Returner's bp_played = opponent's bp_faced. Returner's bp_won = bp_faced - bp_saved.
            if w_bpf and w_bpf > 0:
                # loser was returner against winner: loser faced w_bpf return-side break points
                P[l]["bp_played_returner"] += w_bpf
                P[l]["bp_won_returner"] += (w_bpf - (w_bps or 0))
            if l_bpf and l_bpf > 0:
                P[w]["bp_played_returner"] += l_bpf
                P[w]["bp_won_returner"] += (l_bpf - (l_bps or 0))

            # Hold % = (svgms - bp_lost) / svgms — approx; we use service games and break points
            # converted by returner (= bp_faced - bp_saved).
            if w_svgms and w_svgms > 0:
                breaks_lost = (w_bpf or 0) - (w_bps or 0)
                P[w]["service_games"] += w_svgms
                P[w]["service_games_held"] += max(0, w_svgms - breaks_lost)
            if l_svgms and l_svgms > 0:
                breaks_lost = (l_bpf or 0) - (l_bps or 0)
                P[l]["service_games"] += l_svgms
                P[l]["service_games_held"] += max(0, l_svgms - breaks_lost)

            # Break % = breaks won as returner / opponent service games
            if w_svgms and w_svgms > 0:
                breaks_lost_by_w = (w_bpf or 0) - (w_bps or 0)
                # loser broke winner this many times; loser's return games against winner = w_svgms
                P[l]["return_games"] += w_svgms
                P[l]["return_games_broken"] += breaks_lost_by_w
            if l_svgms and l_svgms > 0:
                breaks_lost_by_l = (l_bpf or 0) - (l_bps or 0)
                P[w]["return_games"] += l_svgms
                P[w]["return_games_broken"] += breaks_lost_by_l

    print(f"Aggregated stats for {len(matches):,} players")

    # Compute per-player normalized stats
    def safe_div(a, b):
        try:
            if not b or b <= 0:
                return None
            return float(a) / float(b)
        except Exception:
            return None

    rows = {}
    for player, n in matches.items():
        if n < MIN_CAREER_MATCHES:
            continue
        d = P[player]
        stats = {}

        stats["tiebreak_win_rate"] = (
            (safe_div(d["tiebreak_wins"], d["tiebreak_played"]) or 0) * 100,
            int(d["tiebreak_played"]),
        )
        stats["deciding_set_wr"] = (
            (safe_div(d["decider_wins"], d["decider_played"]) or 0) * 100,
            int(d["decider_played"]),
        )
        stats["three_set_wr"] = (
            (safe_div(d["three_set_wins"], d["three_set_played"]) or 0) * 100,
            int(d["three_set_played"]),
        )
        stats["vs_top10_wr"] = (
            (safe_div(d["vs_top10_wins"], d["vs_top10_played"]) or 0) * 100,
            int(d["vs_top10_played"]),
        )
        stats["vs_top20_wr"] = (
            (safe_div(d["vs_top20_wins"], d["vs_top20_played"]) or 0) * 100,
            int(d["vs_top20_played"]),
        )
        stats["comeback_rate"] = (
            (safe_div(d["comeback_wins"], d["first_set_lost_total"]) or 0) * 100,
            int(d["first_set_lost_total"]),
        )
        stats["first_set_winner_conv"] = (
            (safe_div(d["first_set_won_match_won"], d["first_set_won_total"]) or 0) * 100,
            int(d["first_set_won_total"]),
        )
        stats["bagels_per_match"] = (
            float(d["bagels_delivered"]) / n,
            n,
        )
        stats["bagels_conceded_per_match"] = (
            float(d["bagels_conceded"]) / n,
            n,
        )
        stats["hold_pct"] = (
            (safe_div(d["service_games_held"], d["service_games"]) or 0) * 100,
            int(d["service_games"]),
        )
        stats["break_pct"] = (
            (safe_div(d["return_games_broken"], d["return_games"]) or 0) * 100,
            int(d["return_games"]),
        )
        stats["bp_save_pct"] = (
            (safe_div(d["bp_saved"], d["bp_faced"]) or 0) * 100,
            int(d["bp_faced"]),
        )
        stats["bp_convert_pct"] = (
            (safe_div(d["bp_won_returner"], d["bp_played_returner"]) or 0) * 100,
            int(d["bp_played_returner"]),
        )
        stats["first_serve_win_pct"] = (
            (safe_div(d["first_serve_won"], d["first_serve_in"]) or 0) * 100,
            int(d["first_serve_in"]),
        )
        stats["second_serve_win_pct"] = (
            (safe_div(d["second_serve_won"], d["second_serve_pts"]) or 0) * 100,
            int(d["second_serve_pts"]),
        )
        stats["aces_per_match"] = (
            float(d["aces_total"]) / max(N[player]["aces_matches"], 1),
            int(N[player]["aces_matches"]),
        )
        stats["df_per_match"] = (
            float(d["dfs_total"]) / max(N[player]["dfs_matches"], 1),
            int(N[player]["dfs_matches"]),
        )

        rows[player] = stats

    # Compute percentiles for each stat
    print(f"Computing percentiles across {len(rows):,} qualifying players")
    out = {}

    # Per-stat min sample-size threshold so we don't compare players with 1 tiebreak vs 100
    MIN_SAMPLE = {
        "tiebreak_win_rate": 30,
        "deciding_set_wr": 20,
        "three_set_wr": 20,
        "vs_top10_wr": 10,
        "vs_top20_wr": 15,
        "comeback_rate": 20,
        "first_set_winner_conv": 50,
        "bagels_per_match": 50,
        "bagels_conceded_per_match": 50,
        "hold_pct": 200,
        "break_pct": 200,
        "bp_save_pct": 100,
        "bp_convert_pct": 100,
        "first_serve_win_pct": 1000,
        "second_serve_win_pct": 500,
        "aces_per_match": 50,
        "df_per_match": 50,
    }

    # Build population per stat
    populations = {}
    for stat in STAT_LABELS:
        cutoff = MIN_SAMPLE[stat]
        vals = [(name, rows[name][stat][0]) for name in rows if rows[name][stat][1] >= cutoff]
        # For df_per_match and bagels_conceded_per_match: lower is better — flip percentile
        populations[stat] = vals

    for player, stats in rows.items():
        out[player] = {}
        for stat, (val, n_samples) in stats.items():
            cutoff = MIN_SAMPLE[stat]
            if n_samples < cutoff:
                continue
            vals = [v for _, v in populations[stat]]
            if not vals:
                continue
            pct = float(percentileofscore(vals, val, kind="mean"))
            sorted_vals = sorted(vals, reverse=True)
            try:
                rank = sorted_vals.index(val) + 1
            except ValueError:
                rank = None
            entry = {
                "value": float(round(val, 2)),
                "sample_size": int(n_samples),
                "percentile": float(round(pct, 1)),
                "rank": int(rank) if rank else None,
                "total_qualifying": int(len(vals)),
            }
            # For "lower is better" stats, invert direction so percentile reflects goodness
            if stat in ("bagels_conceded_per_match", "df_per_match"):
                entry["percentile_raw"] = entry["percentile"]
                entry["percentile"] = float(round(100 - pct, 1))
                # rank should also be inverted (low value = best rank)
                sorted_low_first = sorted(vals)
                try:
                    entry["rank"] = int(sorted_low_first.index(val) + 1)
                except ValueError:
                    pass
            out[player][stat] = entry

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(out, indent=2))
    print(f"Wrote {len(out):,} player percentile profiles to {OUTPUT}")

    # Sanity: show top 5 in tiebreak_win_rate
    tbw = [(n, p["tiebreak_win_rate"]) for n, p in out.items() if "tiebreak_win_rate" in p]
    tbw.sort(key=lambda x: x[1]["value"], reverse=True)
    print("\nTop 10 tiebreak win rate:")
    for name, e in tbw[:10]:
        print(f"  {name:30s} {e['value']:5.1f}% (n={e['sample_size']}, percentile={e['percentile']}, rank=#{e['rank']}/{e['total_qualifying']})")


if __name__ == "__main__":
    main()
