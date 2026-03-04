let surfaceData = null;    // Full JSON: { Hard: { players: {...} }, Clay: ..., Grass: ..., All: ... }
let playersCache = null;
let playerListCache = null;
let currentSurface = 'All';

export async function initCompareData(surface = 'All') {
    currentSurface = surface;

    // Load full surface JSON once
    if (!surfaceData || !playersCache) {
        try {
            const [sRes, plRes] = await Promise.all([
                fetch('data/player_percentiles_by_surface.json'),
                fetch('data/players_v2.json')
            ]);

            if (!sRes.ok) throw new Error("Failed to load surface percentiles");
            if (!plRes.ok) throw new Error("Failed to load players");

            surfaceData = await sRes.json();
            playersCache = await plRes.json();

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
    if (!surfaceData) return null;

    const bucket = surfaceData[currentSurface];
    if (!bucket || !bucket.players) return null;

    const p = bucket.players[playerId];
    if (!p) return null;

    // Find player info
    const playerInfo = playersCache?.find(pl => pl.player_id === playerId);

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
        overall: p.overall ?? null,
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
