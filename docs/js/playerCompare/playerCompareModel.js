let surfaceData = null;    // Full JSON: { Hard: { players: {...} }, Clay: ..., Grass: ..., All: ... }
let playersCache = null;
let playerListCache = null;
let currentSurface = 'All';
let carData = null;        // Elo/CAR ratings

export async function initCompareData(surface = 'All') {
    currentSurface = surface;

    // Load full surface JSON once
    if (!surfaceData || !playersCache) {
        try {
            const [sRes, plRes, carRes] = await Promise.all([
                fetch('data/player_percentiles_by_surface.json'),
                fetch('data/players_v2.json'),
                fetch('data/player_car.json'),
            ]);

            if (!sRes.ok) throw new Error("Failed to load surface percentiles");
            if (!plRes.ok) throw new Error("Failed to load players");

            surfaceData = await sRes.json();
            playersCache = await plRes.json();
            if (carRes.ok) carData = await carRes.json();

            // Build player name list (union of all surfaces)
            const allIds = new Set();
            for (const surfKey of Object.keys(surfaceData)) {
                for (const pid of Object.keys(surfaceData[surfKey].players || {})) {
                    allIds.add(pid);
                }
            }

            const playerLookup = {};
            for (const p of playersCache) {
                playerLookup[p.player_id] = p;
            }

            playerListCache = [...allIds].map(id => ({
                id,
                name: playerLookup[id]?.full_name || id.replace(/_/g, ' ')
            })).sort((a, b) => a.name.localeCompare(b.name));

        } catch (e) {
            console.error("Compare Data Init Error:", e);
            return { percentiles: {}, list: [] };
        }
    }

    return { list: playerListCache };
}

export function setCompareSurface(surface) {
    currentSurface = surface;
}

export function getPlayerList() {
    return playerListCache || [];
}

// ═══════════════════════════════════════════════════════════════
// Metric definitions with tooltips
// ═══════════════════════════════════════════════════════════════
export const METRIC_DEFS = [
    { key: 'serve', label: 'Serve (SQI)', description: 'Serve quality combining ace rate, first-serve win %, and unreturnable serves.' },
    { key: 'return_quality', label: 'Return', description: 'Ability to win points on the opponent\'s serve.' },
    { key: 'ground_consistency', label: 'GS Consistency', description: 'Baseline rally reliability — fewer unforced errors per groundstroke.' },
    { key: 'ground_damage', label: 'GS Damage', description: 'Generating winners and forced errors from baseline play.' },
    { key: 'aggression_efficiency', label: 'Aggression', description: 'How efficiently aggressive play converts into points won.' },
    { key: 'volley_win', label: 'Net Win Rate', description: 'Win rate when approaching and playing at the net.' },
    { key: 'volley_usage', label: 'Net Usage', description: 'How frequently the player comes to the net.' },
    { key: 'break_point_defense', label: 'BP Defense', description: 'Ability to save break points when facing them.' },
    { key: 'endurance', label: 'Endurance', description: 'Performance maintenance in long matches and deep sets.' },
    { key: 'efficiency', label: 'Efficiency', description: 'How quickly points and matches are closed out.' },
    { key: 'aggregate_consistency', label: 'Consistency', description: 'Match-to-match stability of serve and return quality.' },
];

// ═══════════════════════════════════════════════════════════════
// Build compare profile — now returns partial profile when data is missing
// ═══════════════════════════════════════════════════════════════
export function buildCompareProfile(playerId) {
    if (!surfaceData) return null;

    // Find player info
    const playerInfo = playersCache?.find(pl => pl.player_id === playerId);
    const fullName = playerInfo?.full_name || playerId.replace(/_/g, ' ');
    const lastName = playerInfo?.last_name || playerId.split('_').pop();
    const matchesPlayed = playerInfo?.matches_played || 0;

    const bucket = surfaceData[currentSurface];
    const p = bucket?.players?.[playerId];

    // Get All-surface data for comparison (used for data warnings)
    const allBucket = surfaceData['All'];
    const allP = allBucket?.players?.[playerId];
    const allMatchCount = allP ? (playerInfo?.matches_played || 0) : 0;

    // Get CAR data for data warnings
    const car = carData?.[playerId];
    const surfaceKey = currentSurface === 'All' ? 'all' : currentSurface.toLowerCase();
    const surfaceMatches = car ? (car[`matches_${surfaceKey}`] || 0) : 0;

    // If no data in this surface bucket, return partial profile
    if (!p) {
        return {
            playerId,
            fullName,
            lastName,
            countryCode: 'UN',
            imageUrl: `data/players/${playerId}/profile.png`,
            matchesPlayed,
            percentiles: {},
            attributes: METRIC_DEFS.map(def => ({
                key: def.key, label: def.label, description: def.description, value: null, higherIsBetter: true,
            })),
            overall: null,
            hasData: false,
            dataWarning: 'Does not have enough match data on this surface.',
            surfaceMatches: 0,
            allMatches: allMatchCount,
            spsPct: null,
            carPct: null,
        };
    }

    // Percentiles for radar (6 axes)
    const percentiles = {
        serve: p.serve ?? null,
        ground_consistency: p.ground_consistency ?? null,
        aggression_efficiency: p.aggression_efficiency ?? null,
        volley_win: p.volley_win ?? null,
        break_point_defense: p.break_point_defense ?? null,
        aggregate_consistency: p.aggregate_consistency ?? null,
    };

    // All 11 metrics for bar comparison
    const attributes = METRIC_DEFS.map(def => ({
        key: def.key,
        label: def.label,
        description: def.description,
        value: p[def.key] ?? null,
        higherIsBetter: true,
    }));

    // Determine data warning
    let dataWarning = null;
    const spsPct = p.sps_pct ?? null;
    const carPct = p.car_pct ?? null;

    if (currentSurface !== 'All' && surfaceMatches < 15 && allMatchCount > 40) {
        dataWarning = `⚠ May be missing match data — only ${surfaceMatches} matches on ${currentSurface}`;
    } else if (spsPct !== null && carPct !== null && Math.abs(spsPct - carPct) > 30) {
        dataWarning = '⚠ Incomplete data — skill metrics and competition rating diverge significantly';
    }

    return {
        playerId,
        fullName,
        lastName,
        countryCode: 'UN',
        imageUrl: `data/players/${playerId}/profile.png`,
        matchesPlayed,
        percentiles,
        attributes,
        overall: p.overall ?? null,
        hasData: true,
        dataWarning,
        surfaceMatches,
        allMatches: allMatchCount,
        spsPct,
        carPct,
    };
}

// ═══════════════════════════════════════════════════════════════
// Match History — extracts from H2H data
// ═══════════════════════════════════════════════════════════════
export async function fetchH2H(pA, pB) {
    if (!pA || !pB) return null;
    const cacheKey = [pA, pB].sort().join('-vs-');
    if (h2hCache[cacheKey]) return h2hCache[cacheKey];

    try {
        const sortedKey = [pA, pB].sort().join('_vs_');
        const url = `data/h2h/${sortedKey}.json`;
        const resp = await fetch(url);
        if (!resp.ok) return null;
        const data = await resp.json();

        h2hCache[cacheKey] = data;
        return data;
    } catch (e) {
        console.error("Error fetching H2H data:", e);
        return null;
    }
}
