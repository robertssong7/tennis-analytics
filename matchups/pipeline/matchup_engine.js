/**
 * Matchup Engine — Predictive Matchups (Tennis IQ)
 * 
 * Deterministic compatibility scoring per surface.
 * Produces top difficult/favorable matchups with confidence and explanations.
 * 
 * Usage: node matchups/pipeline/matchup_engine.js
 * Output: docs/data/matchups/{player_id}.json
 */

require('dotenv').config();
const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, '..', '..', 'docs', 'data');
const OUT_DIR = path.join(DATA_DIR, 'matchups');

const SURFACES = ['All', 'Hard', 'Clay', 'Grass'];
const TOP_N = 12; // Top difficult + favorable to output
const SHRINKAGE_K = 20; // Surface shrinkage constant for metrics

// ═══════════════════════════════════════════════════════════════
// Model weights for matchup scoring
// ═══════════════════════════════════════════════════════════════
const MODEL_WEIGHTS = {
    elo_gap: 0.35,
    serve_pressure: 0.15,
    return_pressure: 0.15,
    rally_grind: 0.10,
    clutch: 0.10,
    aggression: 0.10,
    net_pressure: 0.05,
};

// ═══════════════════════════════════════════════════════════════
// Sigmoid — converts gap to probability-like value
// ═══════════════════════════════════════════════════════════════
function sigmoid(x) {
    return 1 / (1 + Math.exp(-x));
}

// ═══════════════════════════════════════════════════════════════
// Explanation templates  
// ═══════════════════════════════════════════════════════════════
const REASON_TEMPLATES = {
    elo_gap: {
        positive: (v) => `Overall competition edge — ${v > 0.7 ? 'strong' : 'moderate'} rating advantage.`,
        negative: (v) => `Faces a ${v < 0.3 ? 'significant' : 'moderate'} competition-level disadvantage.`,
    },
    serve_pressure: {
        positive: () => 'Serve quality projects well against opponent\'s return profile.',
        negative: () => 'Opponent\'s return neutralizes the serve advantage on this surface.',
    },
    return_pressure: {
        positive: () => 'Return game effectively disrupts opponent\'s serve patterns.',
        negative: () => 'Opponent\'s serve is difficult to break on this surface.',
    },
    rally_grind: {
        positive: () => 'Baseline consistency and endurance edge in extended rallies.',
        negative: () => 'Opponent\'s grind game (consistency + endurance) is superior.',
    },
    clutch: {
        positive: () => 'Clutch advantage — stronger break-point defense under pressure.',
        negative: () => 'Opponent shows better composure in pressure situations.',
    },
    aggression: {
        positive: () => 'Aggressive play translates more efficiently into points won.',
        negative: () => 'Opponent\'s aggression efficiency edge forces defensive play.',
    },
    net_pressure: {
        positive: () => 'Net approach creates pressure — strong volley conversion rate.',
        negative: () => 'Opponent\'s net game adds an extra dimension to their attack.',
    },
};

// ═══════════════════════════════════════════════════════════════
// Get shrinkage-adjusted metric value
// ═══════════════════════════════════════════════════════════════
function getShrunk(surfacePercentiles, allPercentiles, key, surfaceMatches) {
    const surfVal = surfacePercentiles?.[key] ?? null;
    const allVal = allPercentiles?.[key] ?? null;

    if (surfVal === null && allVal === null) return null;
    if (surfVal === null) return allVal;
    if (allVal === null) return surfVal;

    const lambda = surfaceMatches / (surfaceMatches + SHRINKAGE_K);
    return lambda * surfVal + (1 - lambda) * allVal;
}

// ═══════════════════════════════════════════════════════════════
// Compute matchup score: P(X beats Y | surface)
// ═══════════════════════════════════════════════════════════════
function computeMatchupScore(featuresX, featuresY, eloX, eloY) {
    // Feature gaps (positive = advantage for X)
    const gaps = {};

    // 1. Elo gap → sigmoid
    const eloDiff = (eloX - eloY) / 400;
    gaps.elo_gap = sigmoid(eloDiff);

    // 2. Serve pressure: X's serve vs Y's return
    const servX = featuresX.serve ?? 50;
    const retY = featuresY.return_quality ?? 50;
    gaps.serve_pressure = sigmoid((servX - retY) / 25);

    // 3. Return pressure: X's return vs Y's serve
    const retX = featuresX.return_quality ?? 50;
    const servY = featuresY.serve ?? 50;
    gaps.return_pressure = sigmoid((retX - servY) / 25);

    // 4. Rally grind: (consistency + endurance) gap
    const grindX = ((featuresX.ground_consistency ?? 50) + (featuresX.endurance ?? 50)) / 2;
    const grindY = ((featuresY.ground_consistency ?? 50) + (featuresY.endurance ?? 50)) / 2;
    gaps.rally_grind = sigmoid((grindX - grindY) / 25);

    // 5. Clutch: BP defense gap
    const bpX = featuresX.break_point_defense ?? 50;
    const bpY = featuresY.break_point_defense ?? 50;
    gaps.clutch = sigmoid((bpX - bpY) / 25);

    // 6. Aggression efficiency gap
    const agX = featuresX.aggression_efficiency ?? 50;
    const agY = featuresY.aggression_efficiency ?? 50;
    gaps.aggression = sigmoid((agX - agY) / 25);

    // 7. Net pressure: (volley_win * volley_usage) gap
    const netX = ((featuresX.volley_win ?? 50) * (featuresX.volley_usage ?? 50)) / 100;
    const netY = ((featuresY.volley_win ?? 50) * (featuresY.volley_usage ?? 50)) / 100;
    gaps.net_pressure = sigmoid((netX - netY) / 15);

    // Weighted combination
    let pWin = 0;
    for (const [key, weight] of Object.entries(MODEL_WEIGHTS)) {
        pWin += weight * (gaps[key] || 0.5);
    }

    // Clamp to [0.05, 0.95]
    pWin = Math.max(0.05, Math.min(0.95, pWin));

    return { pWin, gaps };
}

// ═══════════════════════════════════════════════════════════════
// Generate explanation reasons from gaps
// ═══════════════════════════════════════════════════════════════
function generateReasons(gaps, isXPerspective) {
    // Sort gaps by absolute contribution magnitude (distance from 0.5)
    const sorted = Object.entries(gaps)
        .map(([key, val]) => ({
            key,
            val,
            magnitude: Math.abs(val - 0.5) * (MODEL_WEIGHTS[key] || 0),
        }))
        .sort((a, b) => b.magnitude - a.magnitude);

    const reasons = [];
    for (const { key, val } of sorted) {
        if (reasons.length >= 4) break;
        if (Math.abs(val - 0.5) < 0.05) continue; // Skip near-neutral

        const template = REASON_TEMPLATES[key];
        if (!template) continue;

        // From X's perspective: val > 0.5 = positive for X
        const isPositive = isXPerspective ? val > 0.5 : val < 0.5;
        const reason = isPositive ? template.positive(val) : template.negative(val);
        reasons.push(reason);
    }

    return reasons;
}

// ═══════════════════════════════════════════════════════════════
// Confidence level
// ═══════════════════════════════════════════════════════════════
function getConfidence(matchesX, matchesY, pWin) {
    const minMatches = Math.min(matchesX, matchesY);
    const pCertainty = Math.abs(pWin - 0.5);

    if (minMatches >= 30 && pCertainty > 0.1) return 'high';
    if (minMatches >= 10) return 'medium';
    return 'low';
}

// ═══════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════
async function run() {
    console.log('=== Matchup Engine (Tennis IQ) ===\n');

    // Load data
    const percentilesBySurface = JSON.parse(
        fs.readFileSync(path.join(DATA_DIR, 'player_percentiles_by_surface.json'), 'utf8')
    );
    const carData = JSON.parse(
        fs.readFileSync(path.join(DATA_DIR, 'player_car.json'), 'utf8')
    );
    const playersV2 = JSON.parse(
        fs.readFileSync(path.join(DATA_DIR, 'players_v2.json'), 'utf8')
    );

    // Player lookup
    const playerLookup = {};
    for (const p of playersV2) {
        playerLookup[p.player_id] = p;
    }

    const playerEras = fs.existsSync(path.join(DATA_DIR, 'player_eras.json'))
        ? JSON.parse(fs.readFileSync(path.join(DATA_DIR, 'player_eras.json'), 'utf8'))
        : {};

    // Get all player IDs from "All" surface
    const allPlayers = Object.keys(percentilesBySurface.All?.players || {});
    console.log(`  ${allPlayers.length} players in dataset`);

    // Create output directory
    if (!fs.existsSync(OUT_DIR)) {
        fs.mkdirSync(OUT_DIR, { recursive: true });
    }

    let filesWritten = 0;
    const startTime = Date.now();

    // For each player, compute matchup scores against all others
    for (const pid of allPlayers) {
        const playerResult = {};

        for (const surface of SURFACES) {
            const surfKey = surface === 'All' ? 'all' : surface.toLowerCase();
            const bucket = percentilesBySurface[surface]?.players || {};
            const allBucket = percentilesBySurface['All']?.players || {};

            const pX = bucket[pid] || allBucket[pid];
            if (!pX) continue;

            // Get match count on this surface
            const carX = carData[pid];
            const matchesX = carX ? (carX[`matches_${surfKey}`] || 0) : 0;
            const eloX = carX ? (carX[`elo_${surfKey}`] || 1500) : 1500;

            // Build shrinkage-adjusted features for X
            const featX = {};
            const mKeys = ['serve', 'return_quality', 'ground_consistency', 'ground_damage',
                'aggression_efficiency', 'volley_win', 'volley_usage',
                'break_point_defense', 'endurance', 'efficiency', 'aggregate_consistency'];

            for (const k of mKeys) {
                featX[k] = getShrunk(bucket[pid], allBucket[pid], k, matchesX);
            }

            // Score against each opponent
            const matchups = [];
            const eraX = playerEras[pid]?.era_bucket;

            for (const opId of allPlayers) {
                if (opId === pid) continue;

                // Restrict to same era bucket
                const eraY = playerEras[opId]?.era_bucket;
                if (!eraX || !eraY || eraX !== eraY) continue;

                const pY = bucket[opId] || allBucket[opId];
                if (!pY) continue;

                const carY = carData[opId];
                const matchesY = carY ? (carY[`matches_${surfKey}`] || 0) : 0;
                const eloY = carY ? (carY[`elo_${surfKey}`] || 1500) : 1500;

                const featY = {};
                for (const k of mKeys) {
                    featY[k] = getShrunk(bucket[opId], allBucket[opId], k, matchesY);
                }

                const { pWin, gaps } = computeMatchupScore(featX, featY, eloX, eloY);
                const confidence = getConfidence(matchesX, matchesY, pWin);
                const reasons = generateReasons(gaps, true);

                matchups.push({
                    opponent: opId,
                    opponentName: playerLookup[opId]?.full_name || opId.replace(/_/g, ' '),
                    pWin: Math.round(pWin * 100) / 100,
                    confidence,
                    reasons,
                });
            }

            // Sort: difficult = lowest pWin, favorable = highest pWin
            matchups.sort((a, b) => a.pWin - b.pWin);

            playerResult[surface] = {
                difficult: matchups.slice(0, TOP_N).map(m => ({
                    ...m,
                    reasons: generateReasons(
                        computeMatchupScore(
                            featX,
                            getPlayerFeatures(bucket, allBucket, m.opponent, carData, surfKey),
                            eloX,
                            (carData[m.opponent]?.[`elo_${surfKey}`] || 1500)
                        ).gaps,
                        true
                    ),
                })),
                favorable: matchups.slice(-TOP_N).reverse().map(m => ({
                    ...m,
                    reasons: m.reasons, // Already computed from X's perspective
                })),
            };
        }

        // Write player file
        if (Object.keys(playerResult).length > 0) {
            fs.writeFileSync(
                path.join(OUT_DIR, `${pid}.json`),
                JSON.stringify(playerResult, null, 2)
            );
            filesWritten++;
        }
    }

    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    console.log(`  ✓ ${filesWritten} matchup files written to ${OUT_DIR}`);
    console.log(`  ⏱ ${elapsed}s elapsed`);

    // Sanity check: log top difficult and favorable for a known player
    const checkPlayer = 'carlos_alcaraz';
    const checkFile = path.join(OUT_DIR, `${checkPlayer}.json`);
    if (fs.existsSync(checkFile)) {
        const data = JSON.parse(fs.readFileSync(checkFile, 'utf8'));
        console.log(`\n  Sanity check: ${checkPlayer} (All surface)`);
        console.log('    Top 5 Difficult:');
        (data.All?.difficult || []).slice(0, 5).forEach(m => {
            console.log(`      ${m.opponentName}: pWin=${m.pWin} [${m.confidence}]`);
            m.reasons.slice(0, 2).forEach(r => console.log(`        → ${r}`));
        });
        console.log('    Top 5 Favorable:');
        (data.All?.favorable || []).slice(0, 5).forEach(m => {
            console.log(`      ${m.opponentName}: pWin=${m.pWin} [${m.confidence}]`);
        });
    }

    console.log('\n✅ Matchup engine complete!');
}

// Helper: get features for an opponent
function getPlayerFeatures(bucket, allBucket, opId, carData, surfKey) {
    const mKeys = ['serve', 'return_quality', 'ground_consistency', 'ground_damage',
        'aggression_efficiency', 'volley_win', 'volley_usage',
        'break_point_defense', 'endurance', 'efficiency', 'aggregate_consistency'];
    const carY = carData[opId];
    const matchesY = carY ? (carY[`matches_${surfKey}`] || 0) : 0;
    const feat = {};
    for (const k of mKeys) {
        feat[k] = getShrunk(bucket[opId], allBucket[opId], k, matchesY);
    }
    return feat;
}

run().catch(e => { console.error(e); process.exit(1); });
