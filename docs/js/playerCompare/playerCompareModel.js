let percentilesCache = null;
let playersCache = null;
let playerListCache = null;

export async function initCompareData(surface = 'all') {
    if (percentilesCache && playersCache) {
        return { percentiles: percentilesCache, list: playerListCache };
    }

    try {
        const [pRes, plRes] = await Promise.all([
            fetch('data/player_percentiles_all.json'),
            fetch('data/players_v2.json')
        ]);

        if (!pRes.ok) throw new Error("Failed to load percentiles");
        if (!plRes.ok) throw new Error("Failed to load players");

        percentilesCache = await pRes.json(); // { player_id: { serve: 81, ... } }
        playersCache = await plRes.json();    // [{ player_id, full_name, last_name, matches_played }]

        // Build lookup by player_id
        const playerLookup = {};
        for (const p of playersCache) {
            playerLookup[p.player_id] = p;
        }

        playerListCache = Object.keys(percentilesCache).map(id => ({
            id,
            name: playerLookup[id]?.full_name || id.replace(/_/g, ' ')
        })).sort((a, b) => a.name.localeCompare(b.name));

        return { percentiles: percentilesCache, list: playerListCache };
    } catch (e) {
        console.error("Compare Data Init Error:", e);
        return { percentiles: {}, list: [] };
    }
}

export function setCompareSurface(surface) {
    // v2: single surface for now (all)
}

export function getPlayerList() {
    return playerListCache || [];
}

// All 11 metrics with display labels
const METRIC_DEFS = [
    { key: 'serve', label: 'Serve (SQI)' },
    { key: 'return_quality', label: 'Return' },
    { key: 'ground_consistency', label: 'GS Consistency' },
    { key: 'ground_damage', label: 'GS Damage' },
    { key: 'aggression_efficiency', label: 'Aggression' },
    { key: 'volley_win', label: 'Net Win Rate' },
    { key: 'volley_usage', label: 'Net Usage' },
    { key: 'break_point_defense', label: 'BP Defense' },
    { key: 'endurance', label: 'Endurance' },
    { key: 'efficiency', label: 'Efficiency' },
    { key: 'aggregate_consistency', label: 'Consistency' },
];

export function buildCompareProfile(playerId) {
    if (!percentilesCache) return null;

    const p = percentilesCache[playerId];
    if (!p) return null;

    // Find player info
    const playerInfo = playersCache?.find(pl => pl.player_id === playerId);

    // Percentiles for radar (6 axes)
    const percentiles = {
        serve: p.serve,
        ground_consistency: p.ground_consistency,
        aggression_efficiency: p.aggression_efficiency,
        volley_win: p.volley_win,
        break_point_defense: p.break_point_defense,
        aggregate_consistency: p.aggregate_consistency,
    };

    // All 11 metrics for bar comparison
    const attributes = METRIC_DEFS.map(def => ({
        key: def.key,
        label: def.label,
        value: p[def.key] ?? null,
        higherIsBetter: true,
    }));

    return {
        playerId,
        fullName: playerInfo?.full_name || playerId.replace(/_/g, ' '),
        lastName: playerInfo?.last_name || playerId.split('_').pop(),
        countryCode: 'UN',
        imageUrl: `data/players/${playerId}/profile.png`,
        matchesPlayed: playerInfo?.matches_played || 0,
        percentiles,
        attributes,
    };
}

export async function fetchH2H(playerAId, playerBId) {
    const key = [playerAId, playerBId].sort().join('_vs_');
    try {
        const resp = await fetch(`data/h2h/${key}.json`);
        if (!resp.ok) return null;
        return await resp.json();
    } catch {
        return null;
    }
}
