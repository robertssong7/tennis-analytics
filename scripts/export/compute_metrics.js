/**
 * Metrics Computation + Export — Surface-Aware v2
 * 
 * Creates player_match_stats view in PostgreSQL,
 * computes all 11 metrics with percentile rankings (01-100) per surface,
 * computes weighted overall rating mapped to FIFA-like 40-99 scale,
 * and exports JSON files to public/data/.
 * 
 * Checkpoints: A (surface mapping) → B (surface agg) → C (percentiles)
 *              → D (weighted composite) → E (OVR mapping)
 */

require('dotenv').config();
const { Pool } = require('pg');
const fs = require('fs');
const path = require('path');

const { SURFACE_WEIGHTS, mapPercentileToOVR, getSurface } = require('./surface_config');

const pool = new Pool({
    user: process.env.DB_USER,
    password: process.env.DB_PASSWORD,
    host: process.env.DB_HOST,
    port: process.env.DB_PORT,
    database: process.env.DB_NAME,
});

const PUBLIC_DIR = path.join(__dirname, '..', '..', 'docs', 'data');

const METRIC_KEYS = [
    'serve', 'return_quality', 'ground_consistency', 'ground_damage',
    'aggression_efficiency', 'volley_win', 'volley_usage',
    'break_point_defense', 'endurance', 'efficiency', 'aggregate_consistency'
];

const SURFACES = ['Hard', 'Clay', 'Grass'];

// ═══════════════════════════════════════════════════════════════
// Utility: compute percentile rank within a sorted array
// ═══════════════════════════════════════════════════════════════
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

// ═══════════════════════════════════════════════════════════════
// Compute raw metrics from aggregated counts
// ═══════════════════════════════════════════════════════════════
function computeRawMetrics(p) {
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

    // 1. Serve (SQI)
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
        const totalPts = Number(p.total_pts) || 1;
        m.volley_usage = netPts / totalPts;
    }

    // 8. Break Point Defense
    if (bkPts >= 20) {
        m.break_point_defense = bpSaved / bkPts;
    }

    return m;
}

// ═══════════════════════════════════════════════════════════════
// Compute match-level metrics (endurance, efficiency, consistency)
// ═══════════════════════════════════════════════════════════════
function computeMatchLevelMetrics(matches, existingMetrics) {
    const m = existingMetrics;
    if (matches.length < 20) return;

    // 9. Efficiency: straight-set matches / total Bo3 matches
    const bo3 = matches.filter(r => r.sets_played && parseInt(r.sets_played) <= 3);
    const straightSet = bo3.filter(r => parseInt(r.sets_played) === 2);
    if (bo3.length >= 10) {
        m.efficiency = straightSet.length / bo3.length;
    }

    // 10. Endurance: long match ratio
    const longMatches = matches.filter(r => {
        const sp = parseInt(r.sets_played || 0);
        return sp >= 3;
    });
    if (longMatches.length >= 5) {
        m.endurance = longMatches.length / matches.length;
    }

    // 11. Aggregate Consistency: -stddev of match quality
    const qualities = [];
    for (const match of matches) {
        const sp = Number(match.serve_pts) || 0;
        const fi = Number(match.first_in) || 0;
        const fw = Number(match.first_won) || 0;
        const rp = Number(match.return_pts) || 0;
        const rw = Number(match.return_pts_won) || 0;
        if (sp > 0 && fi > 0 && rp > 0) {
            const serveQ = fw / fi;
            const returnQ = rw / rp;
            qualities.push(0.5 * serveQ + 0.5 * returnQ);
        }
    }

    if (qualities.length >= 10) {
        const mean = qualities.reduce((a, b) => a + b, 0) / qualities.length;
        const variance = qualities.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / qualities.length;
        m.aggregate_consistency = -Math.sqrt(variance);
    }
}

// ═══════════════════════════════════════════════════════════════
// Compute percentiles + OVR for a group of players
// ═══════════════════════════════════════════════════════════════
function computePercentilesAndOVR(rawMetrics, surfaceName) {
    const weights = SURFACE_WEIGHTS[surfaceName];

    // 1. Build sorted distributions per metric
    const distributions = {};
    for (const key of METRIC_KEYS) {
        distributions[key] = [];
        for (const pid of Object.keys(rawMetrics)) {
            const val = rawMetrics[pid][key];
            if (val !== undefined && val !== null) {
                distributions[key].push(val);
            }
        }
        distributions[key].sort((a, b) => a - b);
    }

    // 2. Compute percentiles
    const playerPercentiles = {};
    for (const pid of Object.keys(rawMetrics)) {
        const p = {};
        for (const key of METRIC_KEYS) {
            p[key] = computePercentile(rawMetrics[pid][key], distributions[key]);
        }
        playerPercentiles[pid] = p;
    }

    // 3. Compute weighted composite S per player (Option 1: renormalize over available)
    const compositeScores = {};
    for (const [pid, pctls] of Object.entries(playerPercentiles)) {
        let wSum = 0, vSum = 0;
        for (const key of METRIC_KEYS) {
            if (pctls[key] !== null && pctls[key] !== undefined) {
                wSum += weights[key];
                vSum += weights[key] * pctls[key];
            }
        }
        compositeScores[pid] = wSum > 0 ? vSum / wSum : null;
    }

    // 4. Compute percentile rank of S, then map to OVR
    const validScores = Object.values(compositeScores).filter(v => v !== null);
    validScores.sort((a, b) => a - b);

    for (const [pid, S] of Object.entries(compositeScores)) {
        if (S === null) {
            playerPercentiles[pid].overall = null;
            continue;
        }
        // Percentile rank of S within this surface population
        let below = 0;
        for (const v of validScores) {
            if (v < S) below++;
            else if (v === S) below += 0.5;
        }
        const pRank = validScores.length > 1 ? below / (validScores.length - 1) : 0.5;
        playerPercentiles[pid].overall = mapPercentileToOVR(Math.min(1, pRank));
    }

    // 5. Eligibility counts
    const eligibility = {};
    for (const key of METRIC_KEYS) {
        eligibility[key] = distributions[key].length;
    }

    return { playerPercentiles, eligibility };
}


// ═══════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════
async function run() {
    const client = await pool.connect();
    try {
        console.log('=== Surface-Aware Metrics Pipeline ===\n');

        // ─────────────────────────────────────────────────────
        // Create player_match_stats view (same as before)
        // ─────────────────────────────────────────────────────
        console.log('Step 1: Creating player_match_stats view...');
        await client.query(`DROP VIEW IF EXISTS player_match_stats CASCADE`);
        await client.query(`
            CREATE VIEW player_match_stats AS
            SELECT
                o.match_id,
                o.player_id,
                o.player,
                o.serve_pts, o.aces, o.dfs,
                o.first_in, o.first_won, o.second_in, o.second_won,
                o.return_pts, o.return_pts_won,
                o.bk_pts, o.bp_saved,
                o.winners AS total_winners, o.winners_fh, o.winners_bh,
                o.unforced AS total_unforced, o.unforced_fh, o.unforced_bh,
                COALESCE(sb.unret, 0) AS unret,
                COALESCE(sb.forced_err, 0) AS serve_forced_err,
                COALESCE(sb.pts_won_lte_3_shots, 0) AS serve_pts_won_lte_3,
                COALESCE(np.net_pts, 0) AS net_pts,
                COALESCE(np.pts_won, 0) AS net_won,
                COALESCE((SELECT SUM(st.shots) FROM cs_shot_types st
                    WHERE st.match_id = o.match_id AND st.player_id = o.player_id AND st.row = 'F'), 0) AS fh_ground_total,
                COALESCE((SELECT SUM(st.shots) FROM cs_shot_types st
                    WHERE st.match_id = o.match_id AND st.player_id = o.player_id AND st.row = 'B'), 0) AS bh_ground_total,
                COALESCE((SELECT SUM(st.induced_forced) FROM cs_shot_types st
                    WHERE st.match_id = o.match_id AND st.player_id = o.player_id AND st.row = 'F'), 0) AS induced_forced_fh,
                COALESCE((SELECT SUM(st.induced_forced) FROM cs_shot_types st
                    WHERE st.match_id = o.match_id AND st.player_id = o.player_id AND st.row = 'B'), 0) AS induced_forced_bh,
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

        // ─────────────────────────────────────────────────────
        // CHECKPOINT A: Surface mapping
        // ─────────────────────────────────────────────────────
        console.log('\n=== CHECKPOINT A: Surface Mapping ===');

        // Get all match rows with their tournament name
        const allMatchRows = await client.query(`
            SELECT match_id, player_id, player,
                   serve_pts, aces, dfs, first_in, first_won, second_in, second_won,
                   return_pts, return_pts_won, bk_pts, bp_saved,
                   total_winners, winners_fh, winners_bh,
                   total_unforced, unforced_fh, unforced_bh,
                   unret, net_pts, net_won,
                   fh_ground_total, bh_ground_total,
                   induced_forced_fh, induced_forced_bh,
                   sets_played
            FROM player_match_stats
            ORDER BY player_id
        `);

        // Map each match to its surface via tournament name
        const surfaceCounts = { Hard: 0, Clay: 0, Grass: 0, Unknown: 0 };
        const matchSurfaces = {};
        const unmappedTournaments = new Set();

        for (const row of allMatchRows.rows) {
            if (matchSurfaces[row.match_id]) continue;
            const parts = row.match_id.split('-');
            const tourney = parts[2] || '';
            const surface = getSurface(tourney);
            matchSurfaces[row.match_id] = surface;
            surfaceCounts[surface] = (surfaceCounts[surface] || 0) + 1;
        }

        console.log('  Surface distribution (unique matches):');
        for (const [s, c] of Object.entries(surfaceCounts)) {
            if (c > 0) console.log(`    ${s}: ${c}`);
        }
        if (unmappedTournaments.size > 0) {
            console.log(`  ⚠ ${unmappedTournaments.size} unmapped tournaments (defaulted to Hard)`);
        }

        // ─────────────────────────────────────────────────────
        // CHECKPOINT B: Player surface aggregate stats
        // ─────────────────────────────────────────────────────
        console.log('\n=== CHECKPOINT B: Player Surface Aggregates ===');

        // Group match rows by (player_id, surface)
        const playerSurfaceMatches = {}; // { pid: { Hard: [...], Clay: [...], Grass: [...] } }
        const playerAllMatches = {};     // { pid: [...] } for "All" bucket

        for (const row of allMatchRows.rows) {
            const pid = row.player_id;
            const surface = matchSurfaces[row.match_id];

            if (!playerSurfaceMatches[pid]) playerSurfaceMatches[pid] = { Hard: [], Clay: [], Grass: [] };
            if (!playerAllMatches[pid]) playerAllMatches[pid] = [];

            playerSurfaceMatches[pid][surface].push(row);
            playerAllMatches[pid].push(row);
        }

        // Aggregate function: sum counts across matches
        function aggregate(matches) {
            const agg = {
                matches_played: matches.length,
                serve_pts: 0, aces: 0, dfs: 0,
                first_in: 0, first_won: 0, second_in: 0, second_won: 0,
                unret: 0,
                return_pts: 0, return_pts_won: 0,
                bk_pts: 0, bp_saved: 0,
                total_winners: 0, winners_fh: 0, winners_bh: 0,
                total_unforced: 0, unforced_fh: 0, unforced_bh: 0,
                fh_ground_total: 0, bh_ground_total: 0,
                induced_forced_fh: 0, induced_forced_bh: 0,
                net_pts: 0, net_won: 0,
                total_pts: 0,
            };
            for (const r of matches) {
                agg.serve_pts += Number(r.serve_pts) || 0;
                agg.aces += Number(r.aces) || 0;
                agg.dfs += Number(r.dfs) || 0;
                agg.first_in += Number(r.first_in) || 0;
                agg.first_won += Number(r.first_won) || 0;
                agg.second_in += Number(r.second_in) || 0;
                agg.second_won += Number(r.second_won) || 0;
                agg.unret += Number(r.unret) || 0;
                agg.return_pts += Number(r.return_pts) || 0;
                agg.return_pts_won += Number(r.return_pts_won) || 0;
                agg.bk_pts += Number(r.bk_pts) || 0;
                agg.bp_saved += Number(r.bp_saved) || 0;
                agg.total_winners += Number(r.total_winners) || 0;
                agg.winners_fh += Number(r.winners_fh) || 0;
                agg.winners_bh += Number(r.winners_bh) || 0;
                agg.total_unforced += Number(r.total_unforced) || 0;
                agg.unforced_fh += Number(r.unforced_fh) || 0;
                agg.unforced_bh += Number(r.unforced_bh) || 0;
                agg.fh_ground_total += Number(r.fh_ground_total) || 0;
                agg.bh_ground_total += Number(r.bh_ground_total) || 0;
                agg.induced_forced_fh += Number(r.induced_forced_fh) || 0;
                agg.induced_forced_bh += Number(r.induced_forced_bh) || 0;
                agg.net_pts += Number(r.net_pts) || 0;
                agg.net_won += Number(r.net_won) || 0;
                agg.total_pts += (Number(r.serve_pts) || 0) + (Number(r.return_pts) || 0);
            }
            return agg;
        }

        // Sample check: verify aggregates differ by surface
        const samplePid = 'carlos_alcaraz';
        if (playerSurfaceMatches[samplePid]) {
            for (const surf of SURFACES) {
                const m = playerSurfaceMatches[samplePid][surf];
                const a = m.length > 0 ? aggregate(m) : null;
                console.log(`  ${samplePid} ${surf}: ${m.length} matches, serve_pts=${a?.serve_pts || 0}`);
            }
        }

        // ─────────────────────────────────────────────────────
        // CHECKPOINT C: Per-surface metrics + percentiles
        // ─────────────────────────────────────────────────────
        console.log('\n=== CHECKPOINT C: Per-Surface Percentiles ===');

        const surfaceResults = {};
        const coverageBySurface = {};

        for (const surface of SURFACES) {
            console.log(`\n  --- ${surface} ---`);

            // Compute raw metrics for each player on this surface
            const rawMetrics = {};
            let playerCount = 0;

            for (const [pid, surfaceData] of Object.entries(playerSurfaceMatches)) {
                const matches = surfaceData[surface];
                if (!matches || matches.length === 0) continue;

                const agg = aggregate(matches);
                const m = computeRawMetrics(agg);

                // Match-level metrics need individual match rows
                computeMatchLevelMetrics(matches, m);

                rawMetrics[pid] = m;
                playerCount++;
            }

            console.log(`  ${playerCount} players with data`);

            // Compute percentiles and OVR
            const { playerPercentiles, eligibility } = computePercentilesAndOVR(rawMetrics, surface);

            surfaceResults[surface] = playerPercentiles;
            coverageBySurface[surface] = {
                total_players: playerCount,
                eligible_players: eligibility,
            };

            // Log eligibility
            for (const [key, count] of Object.entries(eligibility)) {
                console.log(`    ${key}: ${count} eligible`);
            }

            // Top 5 OVR
            const topOVR = Object.entries(playerPercentiles)
                .filter(([_, p]) => p.overall !== null)
                .sort((a, b) => b[1].overall - a[1].overall)
                .slice(0, 15);
            console.log(`  Top 15 OVR (${surface}):`);
            for (const [pid, p] of topOVR) {
                console.log(`    ${pid}: ${p.overall}`);
            }
        }

        // ─────────────────────────────────────────────────────
        // Also compute "All" surface (existing behavior)
        // ─────────────────────────────────────────────────────
        console.log('\n  --- All (combined) ---');
        const allRawMetrics = {};
        const allPlayers = [];

        for (const [pid, matches] of Object.entries(playerAllMatches)) {
            const agg = aggregate(matches);
            agg.player_id = pid;
            agg.player_name = matches[0]?.player || pid;
            allPlayers.push(agg);

            const m = computeRawMetrics(agg);
            computeMatchLevelMetrics(matches, m);
            allRawMetrics[pid] = m;
        }

        // For "All", use Hard weights as default
        const { playerPercentiles: allPercentiles, eligibility: allEligibility } =
            computePercentilesAndOVR(allRawMetrics, 'Hard');

        console.log(`  ${Object.keys(allRawMetrics).length} players total`);

        // ─────────────────────────────────────────────────────
        // CHECKPOINT D & E: Verify weighted composite + OVR
        // ─────────────────────────────────────────────────────
        console.log('\n=== CHECKPOINT D/E: OVR Sanity ===');
        const checkPlayers = ['carlos_alcaraz', 'novak_djokovic', 'jannik_sinner', 'roger_federer', 'rafael_nadal'];
        for (const pid of checkPlayers) {
            const line = [`  ${pid}:`];
            for (const surface of SURFACES) {
                const ovr = surfaceResults[surface]?.[pid]?.overall;
                line.push(`${surface}=${ovr ?? '-'}`);
            }
            line.push(`All=${allPercentiles[pid]?.overall ?? '-'}`);
            console.log(line.join(' '));
        }

        // ─────────────────────────────────────────────────────
        // Export JSONs
        // ─────────────────────────────────────────────────────
        console.log('\n=== Exporting JSONs ===');

        // 1. player_percentiles_by_surface.json
        const bySurfaceJson = {};
        for (const surface of SURFACES) {
            bySurfaceJson[surface] = { players: surfaceResults[surface] };
        }
        // Include "All" bucket
        bySurfaceJson['All'] = { players: allPercentiles };

        fs.writeFileSync(
            path.join(PUBLIC_DIR, 'player_percentiles_by_surface.json'),
            JSON.stringify(bySurfaceJson, null, 2)
        );
        console.log(`  ✓ player_percentiles_by_surface.json`);

        // 2. Keep player_percentiles_all.json for backward compatibility
        fs.writeFileSync(
            path.join(PUBLIC_DIR, 'player_percentiles_all.json'),
            JSON.stringify(allPercentiles, null, 2)
        );
        console.log(`  ✓ player_percentiles_all.json (backward compat)`);

        // 3. players_v2.json
        const playersJson = allPlayers.map(p => ({
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

        // 4. data_coverage_by_surface.json
        coverageBySurface['All'] = {
            total_players: allPlayers.length,
            eligible_players: allEligibility,
        };
        fs.writeFileSync(
            path.join(PUBLIC_DIR, 'data_coverage_by_surface.json'),
            JSON.stringify(coverageBySurface, null, 2)
        );
        console.log(`  ✓ data_coverage_by_surface.json`);

        // 5. data_coverage.json (backward compat)
        const coverage = {
            total_players: allPlayers.length,
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
            eligible_players: allEligibility,
        };
        fs.writeFileSync(
            path.join(PUBLIC_DIR, 'data_coverage.json'),
            JSON.stringify(coverage, null, 2)
        );
        console.log(`  ✓ data_coverage.json`);

        console.log('\n✅ Surface-aware metrics pipeline complete!');

    } catch (err) {
        console.error('Error:', err.message, err.stack);
        throw err;
    } finally {
        client.release();
        await pool.end();
    }
}

run().catch(() => process.exit(1));
