let percentilesCache = null;
let metaCache = null;
let playerListCache = null;

export async function initCompareData() {
    if (percentilesCache && metaCache) return { percentiles: percentilesCache, meta: metaCache, list: playerListCache };

    try {
        const [pRes, mRes] = await Promise.all([
            fetch('data/player_percentiles_all.json'),
            fetch('data/player_meta.json')
        ]);

        if (!pRes.ok) throw new Error("Failed to load percentiles");
        if (!mRes.ok) throw new Error("Failed to load meta");

        const pData = await pRes.json();
        const mData = await mRes.json();

        percentilesCache = pData.players || {};
        metaCache = mData || {};

        playerListCache = Object.keys(percentilesCache).map(id => ({
            id,
            name: metaCache[id]?.fullName || id.replace(/_/g, ' ')
        })).sort((a, b) => a.name.localeCompare(b.name));

        return { percentiles: percentilesCache, meta: metaCache, list: playerListCache };
    } catch (e) {
        console.error("Compare Data Init Error:", e);
        return { percentiles: {}, meta: {}, list: [] };
    }
}

export function getPlayerList() {
    return playerListCache || [];
}

// Archetype determined by backend in player_meta.json

export function buildCompareProfile(playerId) {
    if (!percentilesCache || !metaCache) return null;

    const p = percentilesCache[playerId];
    const m = metaCache[playerId];

    if (!p) return null; // Player not in dataset

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
