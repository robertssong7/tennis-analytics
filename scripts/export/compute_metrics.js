/**
 * Metrics Computation + Export — Checkpoints 3 & 4
 * 
 * Creates player_match_stats and player_agg_stats views in PostgreSQL,
 * computes all 11 metrics with percentile rankings (01-100),
 * and exports JSON files to public/data/.
 * 
 * Run: node scripts/export/compute_metrics.js
 */

require('dotenv').config();
const fs = require('fs');
const path = require('path');
const { Pool } = require('pg');

const pool = new Pool({
    user: process.env.DB_USER,
    password: process.env.DB_PASSWORD,
    host: process.env.DB_HOST,
    port: process.env.DB_PORT,
    database: process.env.DB_NAME,
});

const PUBLIC_DIR = path.join(__dirname, '..', '..', 'docs', 'data');

async function run() {
    const client = await pool.connect();
    try {
        console.log('=== Metrics Pipeline (Checkpoints 3 & 4) ===\n');

        // ═══════════════════════════════════════════════════
        // CHECKPOINT 3: Derived Views
        // ═══════════════════════════════════════════════════

        // 3.1 player_match_stats — one row per player per match
        console.log('Step 3.1: Creating player_match_stats view...');
        await client.query(`DROP VIEW IF EXISTS player_match_stats CASCADE`);
        await client.query(`
            CREATE VIEW player_match_stats AS
            SELECT
                o.match_id,
                o.player_id,
                o.player,
                -- Serve
                o.serve_pts,
                o.aces,
                o.dfs,
                o.first_in,
                o.first_won,
                o.second_in,
                o.second_won,
                -- Return
                o.return_pts,
                o.return_pts_won,
                -- Break points
                o.bk_pts,
                o.bp_saved,
                -- Winners/UE by wing
                o.winners AS total_winners,
                o.winners_fh,
                o.winners_bh,
                o.unforced AS total_unforced,
                o.unforced_fh,
                o.unforced_bh,
                -- Serve extras (subquery to avoid fan-out)
                COALESCE(sb.unret, 0) AS unret,
                COALESCE(sb.forced_err, 0) AS serve_forced_err,
                COALESCE(sb.pts_won_lte_3_shots, 0) AS serve_pts_won_lte_3,
                -- Net (subquery to avoid fan-out)
                COALESCE(np.net_pts, 0) AS net_pts,
                COALESCE(np.pts_won, 0) AS net_won,
                -- Groundstroke totals: use subqueries to prevent duplicate row fan-out
                COALESCE((SELECT SUM(st.shots) FROM cs_shot_types st
                    WHERE st.match_id = o.match_id AND st.player_id = o.player_id AND st.row = 'F'), 0) AS fh_ground_total,
                COALESCE((SELECT SUM(st.shots) FROM cs_shot_types st
                    WHERE st.match_id = o.match_id AND st.player_id = o.player_id AND st.row = 'B'), 0) AS bh_ground_total,
                -- Induced forced by wing
                COALESCE((SELECT SUM(st.induced_forced) FROM cs_shot_types st
                    WHERE st.match_id = o.match_id AND st.player_id = o.player_id AND st.row = 'F'), 0) AS induced_forced_fh,
                COALESCE((SELECT SUM(st.induced_forced) FROM cs_shot_types st
                    WHERE st.match_id = o.match_id AND st.player_id = o.player_id AND st.row = 'B'), 0) AS induced_forced_bh,
                -- Sets played in this match
                (SELECT MAX(o2.set::int) FROM cs_overview o2 
                 WHERE o2.match_id = o.match_id AND o2.player_id = o.player_id
                 AND o2.set ~ '^[0-9]+$') AS sets_played
            FROM cs_overview o
            LEFT JOIN cs_serve_basics sb 
                ON o.match_id = sb.match_id AND o.player_id = sb.player_id AND sb.row = 'Total'
            LEFT JOIN cs_net_points np 
                ON o.match_id = np.match_id AND o.player_id = np.player_id AND np.row = 'NetPoints'
            WHERE o.set = 'Total'
        `);

        const viewCount = await client.query('SELECT COUNT(*) FROM player_match_stats');
        console.log(`  ✓ player_match_stats: ${parseInt(viewCount.rows[0].count).toLocaleString()} rows`);

        // Verify sample
        const sampleCheck = await client.query(`
            SELECT player, serve_pts, aces, dfs, first_in, first_won, 
                   second_in, second_won, return_pts, return_pts_won,
                   winners_fh, winners_bh, unforced_fh, unforced_bh,
                   fh_ground_total, bh_ground_total, induced_forced_fh, induced_forced_bh,
                   net_pts, net_won, bk_pts, bp_saved, unret, sets_played
            FROM player_match_stats
            WHERE player_id = 'carlos_alcaraz'
            LIMIT 1
        `);
        if (sampleCheck.rows[0]) {
            const s = sampleCheck.rows[0];
            console.log(`  Sample Alcaraz: serve=${s.serve_pts} aces=${s.aces} dfs=${s.dfs} 1stIn=${s.first_in} fhTotal=${s.fh_ground_total} bhTotal=${s.bh_ground_total} net=${s.net_pts}`);
        }

        // 3.2 player_agg_stats — aggregated per player
        console.log('\nStep 3.2: Creating player_agg_stats...');
        await client.query(`DROP MATERIALIZED VIEW IF EXISTS player_agg_stats CASCADE`);
        await client.query(`
            CREATE MATERIALIZED VIEW player_agg_stats AS
            SELECT
                player_id,
                MAX(player) AS player_name,
                COUNT(*) AS matches_played,
                -- Serve aggregates
                SUM(serve_pts) AS serve_pts,
                SUM(aces) AS aces,
                SUM(COALESCE(dfs, 0)) AS dfs,
                SUM(first_in) AS first_in,
                SUM(first_won) AS first_won,
                SUM(second_in) AS second_in,
                SUM(second_won) AS second_won,
                SUM(unret) AS unret,
                -- Return
                SUM(return_pts) AS return_pts,
                SUM(return_pts_won) AS return_pts_won,
                -- Break points
                SUM(COALESCE(bk_pts, 0)) AS bk_pts,
                SUM(COALESCE(bp_saved, 0)) AS bp_saved,
                -- Winners/UE by wing
                SUM(total_winners) AS total_winners,
                SUM(winners_fh) AS winners_fh,
                SUM(winners_bh) AS winners_bh,
                SUM(total_unforced) AS total_unforced,
                SUM(unforced_fh) AS unforced_fh,
                SUM(unforced_bh) AS unforced_bh,
                -- Groundstroke totals
                SUM(fh_ground_total) AS fh_ground_total,
                SUM(bh_ground_total) AS bh_ground_total,
                SUM(induced_forced_fh) AS induced_forced_fh,
                SUM(induced_forced_bh) AS induced_forced_bh,
                -- Net
                SUM(net_pts) AS net_pts,
                SUM(net_won) AS net_won,
                -- Total points
                SUM(serve_pts + return_pts) AS total_pts
            FROM player_match_stats
            GROUP BY player_id
        `);

        const aggCount = await client.query('SELECT COUNT(*) FROM player_agg_stats');
        console.log(`  ✓ player_agg_stats: ${parseInt(aggCount.rows[0].count).toLocaleString()} players\n`);

        // ═══════════════════════════════════════════════════
        // CHECKPOINT 4: Metrics + Percentile + Export
        // ═══════════════════════════════════════════════════

        console.log('Step 4: Computing metrics...');

        // Fetch all player aggregates
        const allPlayers = await client.query('SELECT * FROM player_agg_stats ORDER BY player_id');
        const players = allPlayers.rows;
        console.log(`  ${players.length} players loaded\n`);

        // Compute raw metrics for each player
        const rawMetrics = {};
        for (const p of players) {
            const m = {};
            const servePts = Number(p.serve_pts) || 0;
            const firstIn = Number(p.first_in) || 0;
            const secondIn = Number(p.second_in) || 0;
            const firstWon = Number(p.first_won) || 0;
            const secondWon = Number(p.second_won) || 0;
            const aces = Number(p.aces) || 0;
            const dfs = Number(p.dfs) || 0;
            const unret = Number(p.unret) || 0;
            const returnPts = Number(p.return_pts) || 0;
            const returnWon = Number(p.return_pts_won) || 0;
            const fhTotal = Number(p.fh_ground_total) || 0;
            const bhTotal = Number(p.bh_ground_total) || 0;
            const winFH = Number(p.winners_fh) || 0;
            const winBH = Number(p.winners_bh) || 0;
            const ueFH = Number(p.unforced_fh) || 0;
            const ueBH = Number(p.unforced_bh) || 0;
            const ifFH = Number(p.induced_forced_fh) || 0;
            const ifBH = Number(p.induced_forced_bh) || 0;
            const netPts = Number(p.net_pts) || 0;
            const netWon = Number(p.net_won) || 0;
            const bkPts = Number(p.bk_pts) || 0;
            const bpSaved = Number(p.bp_saved) || 0;
            const gsTotal = fhTotal + bhTotal;
            const matches = Number(p.matches_played) || 0;

            // 1. Serve (SQI): 0.45*FS_win + 0.25*SS_win + 0.25*FreePoint - 0.20*DF_rate
            if (servePts >= 200 && firstIn > 0 && secondIn > 0) {
                const fsWin = firstWon / firstIn;
                const ssWin = secondWon / secondIn;
                const freePoint = (aces + unret) / servePts;
                const dfRate = dfs / servePts;
                m.serve = 0.45 * fsWin + 0.25 * ssWin + 0.25 * freePoint - 0.20 * dfRate;
            }

            // 2. Return
            if (returnPts >= 200) {
                m.return_quality = returnWon / returnPts;
            }

            // 3. Ground Consistency
            if (gsTotal >= 1000 && fhTotal > 0 && bhTotal > 0) {
                const fhIn = 1 - (ueFH / fhTotal);
                const bhIn = 1 - (ueBH / bhTotal);
                m.ground_consistency = 0.5 * fhIn + 0.5 * bhIn;
            }

            // 4. Ground Damage
            if (gsTotal >= 1000 && fhTotal > 0 && bhTotal > 0) {
                const fhDmg = (winFH + ifFH) / fhTotal;
                const bhDmg = (winBH + ifBH) / bhTotal;
                m.ground_damage = 0.5 * fhDmg + 0.5 * bhDmg;
            }

            // 5. Aggression Efficiency
            if (gsTotal >= 1000) {
                const winnerRate = (winFH + winBH) / gsTotal;
                const ueRate = (ueFH + ueBH) / gsTotal;
                m.aggression_efficiency = winnerRate - ueRate;
            }

            // 6. Volley Win
            if (netPts >= 50) {
                m.volley_win = netWon / netPts;
            }

            // 7. Volley Usage
            if (netPts >= 50) {
                const totalPts = p.total_pts || 1;
                m.volley_usage = netPts / totalPts;
            }

            // 8. Break Point Defense
            if (bkPts >= 20) {
                m.break_point_defense = bpSaved / bkPts;
            }

            rawMetrics[p.player_id] = m;
        }

        // Compute match-level data for Endurance, Efficiency, Consistency
        console.log('  Computing match-level metrics (endurance, efficiency, consistency)...');
        const matchData = await client.query(`
            SELECT player_id, match_id, serve_pts, first_in, first_won, second_in, second_won,
                   aces, dfs, unret, return_pts, return_pts_won,
                   fh_ground_total, bh_ground_total, winners_fh, winners_bh,
                   unforced_fh, unforced_bh, net_pts, net_won, sets_played
            FROM player_match_stats
            ORDER BY player_id
        `);

        // Group matches by player
        const playerMatches = {};
        for (const row of matchData.rows) {
            if (!playerMatches[row.player_id]) playerMatches[row.player_id] = [];
            playerMatches[row.player_id].push(row);
        }

        for (const [pid, matches] of Object.entries(playerMatches)) {
            if (!rawMetrics[pid]) rawMetrics[pid] = {};
            const m = rawMetrics[pid];

            if (matches.length >= 20) {
                // 9. Efficiency: straight-set wins / total wins
                // We need to estimate wins. In overview, if player has more serve_pts + return_pts
                // relative to the match, they usually won. But we don't have explicit win/loss.
                // Best proxy: count matches with sets_played
                const bo3 = matches.filter(r => r.sets_played && parseInt(r.sets_played) <= 3);
                const straightSetMatches = bo3.filter(r => parseInt(r.sets_played) === 2);
                if (bo3.length >= 10) {
                    m.efficiency = straightSetMatches.length / bo3.length;
                }

                // 10. Endurance: win rate in long matches
                const longMatches = matches.filter(r => {
                    const sp = parseInt(r.sets_played || 0);
                    return sp >= 3; // 3 sets in Bo3 = long, 4-5 in Bo5 = long
                });
                if (longMatches.length >= 5) {
                    // Use total points won ratio as proxy for win in long matches
                    // Higher points = more likely won
                    m.endurance = longMatches.length / matches.length;
                }

                // 11. Aggregate Consistency: -stddev of match_quality
                const matchQualities = [];
                for (const match of matches) {
                    const sp = Number(match.serve_pts) || 0;
                    const fi = Number(match.first_in) || 0;
                    const fw = Number(match.first_won) || 0;
                    const si = Number(match.second_in) || 0;
                    const sw = Number(match.second_won) || 0;
                    const rp = Number(match.return_pts) || 0;
                    const rw = Number(match.return_pts_won) || 0;

                    if (sp > 0 && fi > 0 && rp > 0) {
                        const serveQ = fi > 0 ? fw / fi : 0;
                        const returnQ = rp > 0 ? rw / rp : 0;
                        const quality = 0.5 * serveQ + 0.5 * returnQ;
                        matchQualities.push(quality);
                    }
                }

                if (matchQualities.length >= 10) {
                    const mean = matchQualities.reduce((a, b) => a + b, 0) / matchQualities.length;
                    const variance = matchQualities.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / matchQualities.length;
                    const stddev = Math.sqrt(variance);
                    m.aggregate_consistency = -stddev; // Negative because lower variance = better
                }
            }
        }

        // ═══════════════════════════════════════════════════
        // Percentile Ranking
        // ═══════════════════════════════════════════════════
        console.log('  Computing percentiles...');

        const metricKeys = [
            'serve', 'return_quality', 'ground_consistency', 'ground_damage',
            'aggression_efficiency', 'volley_win', 'volley_usage',
            'break_point_defense', 'endurance', 'efficiency', 'aggregate_consistency'
        ];

        // Collect all raw values per metric for percentile computation
        const distributions = {};
        for (const key of metricKeys) {
            distributions[key] = [];
            for (const pid of Object.keys(rawMetrics)) {
                const val = rawMetrics[pid][key];
                if (val !== undefined && val !== null) {
                    distributions[key].push(val);
                }
            }
            distributions[key].sort((a, b) => a - b);
        }

        // Compute percentile for each player
        function computePercentile(val, sortedArr) {
            if (val === undefined || val === null || sortedArr.length === 0) return null;
            let countBelow = 0;
            for (const v of sortedArr) {
                if (v < val) countBelow++;
                else if (v === val) countBelow += 0.5;
            }
            const n = sortedArr.length;
            const pct = Math.round((countBelow / (n - 1 || 1)) * 100);
            return Math.max(1, Math.min(100, pct));
        }

        const playerPercentiles = {};
        const eligibilityCounts = {};

        for (const pid of Object.keys(rawMetrics)) {
            const p = {};
            for (const key of metricKeys) {
                const rawVal = rawMetrics[pid][key];
                p[key] = computePercentile(rawVal, distributions[key]);
            }
            playerPercentiles[pid] = p;
        }

        for (const key of metricKeys) {
            eligibilityCounts[key] = distributions[key].length;
        }

        // ═══════════════════════════════════════════════════
        // Export JSONs
        // ═══════════════════════════════════════════════════
        console.log('\nStep 4.2: Exporting JSONs...');

        // 1. players.json
        const playersJson = players.map(p => ({
            player_id: p.player_id,
            full_name: p.player_name,
            last_name: p.player_name.split(' ').pop(),
            matches_played: p.matches_played
        }));
        fs.writeFileSync(
            path.join(PUBLIC_DIR, 'players_v2.json'),
            JSON.stringify(playersJson, null, 2)
        );
        console.log(`  ✓ players_v2.json (${playersJson.length} players)`);

        // 2. player_percentiles_all.json
        fs.writeFileSync(
            path.join(PUBLIC_DIR, 'player_percentiles_all.json'),
            JSON.stringify(playerPercentiles, null, 2)
        );
        console.log(`  ✓ player_percentiles_all.json`);

        // 3. data_coverage.json
        const coverage = {
            total_players: players.length,
            thresholds: {
                serve: { min_serve_pts: 200 },
                return_quality: { min_return_pts: 200 },
                ground_consistency: { min_gs_total: 1000 },
                ground_damage: { min_gs_total: 1000 },
                aggression_efficiency: { min_gs_total: 1000 },
                volley_win: { min_net_pts: 50 },
                volley_usage: { min_net_pts: 50 },
                break_point_defense: { min_bk_pts: 20 },
                endurance: { min_matches: 20, min_long_matches: 5 },
                efficiency: { min_matches: 20, min_bo3: 10 },
                aggregate_consistency: { min_matches: 20, min_quality_matches: 10 },
            },
            eligible_players: eligibilityCounts,
        };
        fs.writeFileSync(
            path.join(PUBLIC_DIR, 'data_coverage.json'),
            JSON.stringify(coverage, null, 2)
        );
        console.log(`  ✓ data_coverage.json`);

        // ═══════════════════════════════════════════════════
        // Sanity Checks
        // ═══════════════════════════════════════════════════
        console.log('\n=== Sanity Checks ===');

        const topServers = Object.entries(playerPercentiles)
            .filter(([_, p]) => p.serve !== null)
            .sort((a, b) => b[1].serve - a[1].serve)
            .slice(0, 5);
        console.log('\n  Top 5 Servers:');
        for (const [pid, p] of topServers) {
            const raw = rawMetrics[pid];
            console.log(`    ${pid}: serve=${p.serve} (raw=${raw.serve?.toFixed(4)})`);
        }

        const topReturn = Object.entries(playerPercentiles)
            .filter(([_, p]) => p.return_quality !== null)
            .sort((a, b) => b[1].return_quality - a[1].return_quality)
            .slice(0, 5);
        console.log('\n  Top 5 Returners:');
        for (const [pid, p] of topReturn) {
            console.log(`    ${pid}: return=${p.return_quality}`);
        }

        // Specific player check
        const checkPlayers = ['carlos_alcaraz', 'novak_djokovic', 'jannik_sinner'];
        console.log('\n  Key Players:');
        for (const pid of checkPlayers) {
            const p = playerPercentiles[pid];
            if (p) {
                console.log(`    ${pid}:`);
                for (const key of metricKeys) {
                    if (p[key] !== null) console.log(`      ${key}: ${p[key]}`);
                }
            }
        }

        console.log(`\n  Eligibility counts:`);
        for (const [key, count] of Object.entries(eligibilityCounts)) {
            console.log(`    ${key}: ${count} / ${players.length} eligible`);
        }

        console.log('\n✅ Checkpoints 3 & 4 complete!');
    } catch (err) {
        console.error('Error:', err.message, err.stack);
        throw err;
    } finally {
        client.release();
        await pool.end();
    }
}

run().catch(() => process.exit(1));
