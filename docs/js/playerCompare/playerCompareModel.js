let percentilesCache = {};  // keyed by surface
let metaCache = {};         // keyed by surface
let playerListCache = null;
let currentCompareSurface = 'all';

export async function initCompareData(surface = 'all') {
    const cacheKey = surface;
    if (percentilesCache[cacheKey] && metaCache[cacheKey]) {
        return { percentiles: percentilesCache[cacheKey], meta: metaCache[cacheKey], list: playerListCache };
    }

    try {
        const suffix = surface === 'all' ? '_all' : `_${surface.toLowerCase()}`;
        let pRes, mRes;

        try {
            [pRes, mRes] = await Promise.all([
                fetch(`data/player_percentiles${suffix}.json`),
                fetch(`data/player_meta${suffix}.json`)
            ]);
            if (!pRes.ok || !mRes.ok) throw new Error('New naming not found');
        } catch {
            // Fallback to legacy naming convention
            [pRes, mRes] = await Promise.all([
                fetch(`data/player_percentiles_all.json`),
                fetch(`data/player_meta.json`)
            ]);
        }

        if (!pRes.ok) throw new Error("Failed to load percentiles");
        if (!mRes.ok) throw new Error("Failed to load meta");

        const pData = await pRes.json();
        const mData = await mRes.json();

        percentilesCache[cacheKey] = pData.players || {};
        metaCache[cacheKey] = mData || {};

        // Build player list from 'all' surface (superset)
        if (!playerListCache || cacheKey === 'all') {
            playerListCache = Object.keys(percentilesCache[cacheKey]).map(id => ({
                id,
                name: metaCache[cacheKey][id]?.fullName || id.replace(/_/g, ' ')
            })).sort((a, b) => a.name.localeCompare(b.name));
        }

        return { percentiles: percentilesCache[cacheKey], meta: metaCache[cacheKey], list: playerListCache };
    } catch (e) {
        console.error("Compare Data Init Error:", e);
        return { percentiles: {}, meta: {}, list: [] };
    }
}

export function setCompareSurface(surface) {
    currentCompareSurface = surface;
}

export function getPlayerList() {
    return playerListCache || [];
}

export function buildCompareProfile(playerId) {
    const surface = currentCompareSurface;
    const perc = percentilesCache[surface];
    const met = metaCache[surface];

    if (!perc || !met) return null;

    const p = perc[playerId];
    const m = met[playerId];

    if (!p) return null; // Player not in dataset for this surface

    const radar = {
        serve: p.serve,
        forehand: p.forehand,
        backhand: p.backhand,
        pace: p.pace,
        consistency: p.consistency
    };

    const attributes = [
        { key: 'serve', label: 'Serve', value: p.serve, higherIsBetter: true },
        { key: 'serve_plus_1', label: 'Serve+1', value: p.serve_plus_1, higherIsBetter: true },
        { key: 'forehand', label: 'Forehand', value: p.forehand, higherIsBetter: true },
        { key: 'backhand', label: 'Backhand', value: p.backhand, higherIsBetter: true },
        { key: 'volley_net', label: 'Volley / Net', value: p.volley_net, higherIsBetter: true },
        { key: 'defense', label: 'Defense', value: p.defense, higherIsBetter: true },
        { key: 'touch', label: 'Touch / Finesse', value: p.touch, higherIsBetter: true },
        { key: 'pace', label: 'Pace', value: p.pace, higherIsBetter: true },
        { key: 'consistency', label: 'Consistency', value: p.consistency, higherIsBetter: true },
        { key: 'balance', label: 'Balance', value: p.balance, higherIsBetter: true },
        { key: 'physical', label: 'Physical', value: null, higherIsBetter: true, note: 'Coming soon' },
        { key: 'trick', label: 'Trick', value: null, higherIsBetter: true, note: 'Coming soon' }
    ];

    return {
        playerId,
        fullName: m?.fullName || playerId.replace(/_/g, ' '),
        lastName: m?.lastName || playerId.split('_').pop(),
        countryCode: m?.countryCode || 'UN',
        imageUrl: `data/players/${playerId}/profile.png`,
        elo: p.elo || 0,
        archetype: m?.playstyle || 'All-Round',
        radar,
        attributes,
        meta: {
            age: m?.age,
            hometown: m?.hometown,
            playstyle: m?.playstyle,
            racket: m?.racket
        }
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
