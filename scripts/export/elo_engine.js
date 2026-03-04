/**
 * Elo Engine — Competition-Adjusted Rating (CAR)
 * 
 * Extracts match list from cs_overview, infers winners, and runs
 * Elo ratings chronologically with tournament-tier weighting.
 * Produces per-player ratings: R_all, R_hard, R_clay, R_grass.
 * 
 * Usage: const { runElo } = require('./elo_engine');
 *        const eloResults = await runElo(pool);
 */

const { getSurface, getTournamentTier } = require('./surface_config');

const ELO_K = 32;          // Base K-factor
const ELO_INIT = 1500;     // Starting rating for all players
const SHRINKAGE_K = 10;    // Shrinkage constant for surface fallback

// ═══════════════════════════════════════════════════════════════
// Parse match_id into structured fields
// ═══════════════════════════════════════════════════════════════
// Format: YYYYMMDD-M-Tournament-Round-Player1-Player2
// Player names may contain underscores, so we handle multi-segment names
function parseMatchId(matchId) {
    const parts = matchId.split('-');
    if (parts.length < 6) return null;

    const dateStr = parts[0];       // YYYYMMDD
    // parts[1] = 'M' (men's)
    const tournament = parts[2];     // Tournament name
    const round = parts[3];          // Round (F, SF, QF, R16, etc.)

    // Remaining segments are Player1_First_Last-Player2_First_Last
    // Rejoin from index 4 and split by the last '-' between two capitalized names
    const playerPart = parts.slice(4).join('-');

    // Find the split point: look for '-' followed by a capital letter
    // This handles names like "Juan_Carlos_Ferrero-Andre_Agassi"
    let splitIdx = -1;
    for (let i = 1; i < playerPart.length - 1; i++) {
        if (playerPart[i] === '-' && playerPart[i + 1] >= 'A' && playerPart[i + 1] <= 'Z') {
            // Check that the char before '-' is a letter (not another separator)
            splitIdx = i;
            // Don't break — we want the LAST valid split (in case of hyphenated names)
            // Actually, for Sackmann format Player1_Name-Player2_Name, the first '-' after index 4 works
            // because player names use underscores, not hyphens
            break; // First '-' is the split between players
        }
    }

    if (splitIdx === -1) return null;

    const player1Raw = playerPart.substring(0, splitIdx);
    const player2Raw = playerPart.substring(splitIdx + 1);

    // Convert to player_id format: lowercase, underscores
    const player1 = player1Raw.toLowerCase().replace(/[^a-z0-9_]/g, '');
    const player2 = player2Raw.toLowerCase().replace(/[^a-z0-9_]/g, '');

    return {
        dateStr,
        date: new Date(
            parseInt(dateStr.substring(0, 4)),
            parseInt(dateStr.substring(4, 6)) - 1,
            parseInt(dateStr.substring(6, 8))
        ),
        tournament,
        round,
        surface: getSurface(tournament),
        tier: getTournamentTier(tournament),
        player1,
        player2,
        player1Raw,
        player2Raw,
    };
}

// ═══════════════════════════════════════════════════════════════
// Make player_id from raw name (same as build_postgres.js)
// ═══════════════════════════════════════════════════════════════
function makePlayerId(name) {
    return name.toLowerCase()
        .replace(/[''`]/g, '')
        .replace(/[^a-z0-9\s]/g, '')
        .replace(/\s+/g, '_')
        .trim();
}

// ═══════════════════════════════════════════════════════════════
// Extract match list and determine winners from cs_overview
// ═══════════════════════════════════════════════════════════════
async function extractMatches(pool) {
    console.log('  Extracting match list from cs_overview...');

    // Get Total rows for all matches — both players per match
    const res = await pool.query(`
        SELECT match_id, player_id, player,
               COALESCE(first_won, 0) + COALESCE(second_won, 0) AS serve_won,
               COALESCE(return_pts_won, 0) AS return_won
        FROM cs_overview
        WHERE set = 'Total'
        ORDER BY match_id, player_id
    `);

    // Group by match_id
    const matchMap = {};
    for (const row of res.rows) {
        if (!matchMap[row.match_id]) matchMap[row.match_id] = [];
        matchMap[row.match_id].push({
            player_id: row.player_id,
            player: row.player,
            totalWon: (parseInt(row.serve_won) || 0) + (parseInt(row.return_won) || 0),
        });
    }

    const matches = [];
    let skipped = 0;

    for (const [matchId, players] of Object.entries(matchMap)) {
        // Need exactly 2 players
        if (players.length !== 2) {
            skipped++;
            continue;
        }

        const parsed = parseMatchId(matchId);
        if (!parsed) {
            skipped++;
            continue;
        }

        // Winner = player with more total points won
        const [p1, p2] = players;
        let winner, loser;
        if (p1.totalWon > p2.totalWon) {
            winner = p1.player_id;
            loser = p2.player_id;
        } else if (p2.totalWon > p1.totalWon) {
            winner = p2.player_id;
            loser = p1.player_id;
        } else {
            // Tie in total points — shouldn't happen but default to first player in match_id
            winner = parsed.player1;
            loser = parsed.player2;
        }

        matches.push({
            matchId,
            date: parsed.date,
            dateStr: parsed.dateStr,
            tournament: parsed.tournament,
            round: parsed.round,
            surface: parsed.surface,
            tier: parsed.tier,
            winner,
            loser,
        });
    }

    // Sort chronologically
    matches.sort((a, b) => a.date - b.date || a.matchId.localeCompare(b.matchId));

    console.log(`  Extracted ${matches.length} matches (${skipped} skipped)`);
    console.log(`  Date range: ${matches[0]?.dateStr} to ${matches[matches.length - 1]?.dateStr}`);

    return matches;
}

// ═══════════════════════════════════════════════════════════════
// Run Elo engine
// ═══════════════════════════════════════════════════════════════
async function runElo(pool) {
    console.log('\n=== Elo Engine (CAR) ===');

    const matches = await extractMatches(pool);

    // Initialize ratings
    const ratings = {}; // { playerId: { all: R, Hard: R, Clay: R, Grass: R, matches_all: N, matches_Hard: N, ... } }

    function getOrInit(pid) {
        if (!ratings[pid]) {
            ratings[pid] = {
                all: ELO_INIT,
                Hard: ELO_INIT,
                Clay: ELO_INIT,
                Grass: ELO_INIT,
                matches_all: 0,
                matches_Hard: 0,
                matches_Clay: 0,
                matches_Grass: 0,
            };
        }
        return ratings[pid];
    }

    // Process matches in chronological order
    for (const match of matches) {
        const w = getOrInit(match.winner);
        const l = getOrInit(match.loser);

        // Update overall rating
        const eW_all = 1 / (1 + Math.pow(10, (l.all - w.all) / 400));
        const eL_all = 1 - eW_all;
        const kEff = ELO_K * match.tier;

        w.all += kEff * (1 - eW_all);
        l.all += kEff * (0 - eL_all);
        w.matches_all++;
        l.matches_all++;

        // Update surface-specific rating
        const surf = match.surface;
        if (surf === 'Hard' || surf === 'Clay' || surf === 'Grass') {
            const eW_s = 1 / (1 + Math.pow(10, (l[surf] - w[surf]) / 400));
            const eL_s = 1 - eW_s;

            w[surf] += kEff * (1 - eW_s);
            l[surf] += kEff * (0 - eL_s);
            w[`matches_${surf}`]++;
            l[`matches_${surf}`]++;
        }
    }

    // Apply shrinkage for surface ratings with few matches
    for (const [pid, r] of Object.entries(ratings)) {
        for (const surf of ['Hard', 'Clay', 'Grass']) {
            const n = r[`matches_${surf}`];
            if (n < SHRINKAGE_K * 2) {
                const lambda = n / (n + SHRINKAGE_K);
                r[surf] = lambda * r[surf] + (1 - lambda) * r.all;
            }
        }
    }

    // Log stats
    const allRatings = Object.values(ratings).map(r => r.all);
    allRatings.sort((a, b) => a - b);
    const mean = allRatings.reduce((a, b) => a + b, 0) / allRatings.length;
    console.log(`  ${Object.keys(ratings).length} players rated`);
    console.log(`  R_all mean: ${mean.toFixed(1)}, min: ${allRatings[0].toFixed(1)}, max: ${allRatings[allRatings.length - 1].toFixed(1)}`);

    // Top 15
    const topAll = Object.entries(ratings)
        .sort((a, b) => b[1].all - a[1].all)
        .slice(0, 15);
    console.log('  Top 15 Elo (all):');
    for (const [pid, r] of topAll) {
        console.log(`    ${pid}: ${r.all.toFixed(1)} (${r.matches_all} matches)`);
    }

    return ratings;
}

// ═══════════════════════════════════════════════════════════════
// Compute percentile rank of an Elo rating within a distribution
// ═══════════════════════════════════════════════════════════════
function computeEloPercentile(rating, sortedRatings) {
    if (sortedRatings.length === 0) return 50;
    let below = 0;
    for (const v of sortedRatings) {
        if (v < rating) below++;
        else if (v === rating) below += 0.5;
    }
    const pct = Math.round((below / (sortedRatings.length - 1 || 1)) * 100);
    return Math.max(1, Math.min(100, pct));
}

module.exports = {
    runElo,
    computeEloPercentile,
    SHRINKAGE_K,
};
