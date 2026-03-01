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

function determineArchetype(p) {
    const srv = p.serve || 0;
    const srv1 = p.serve_plus_1 || 0;
    const def = p.defense || 0;
    const con = p.consistency || 0;
    const fh = p.forehand || 0;
    const bh = p.backhand || 0;
    const vol = p.volley_net || 0;

    if (srv >= 80 && srv1 >= 65) return "Big Server";
    if (def >= 80 && con >= 70 && srv < 60) return "Counterpuncher";
    if (srv >= 60 && fh >= 60 && bh >= 60 && vol >= 60) return "All-Court";
    if ((fh >= 75 || bh >= 75) && vol < 60) return "Aggressive Baseliner";

    return "All-Round";
}

export function buildCompareProfile(playerId) {
    if (!percentilesCache || !metaCache) return null;

    const p = percentilesCache[playerId];
    const m = metaCache[playerId];

    if (!p) return null; // Player not in dataset

    const radar = {
        serve: p.serve !== null ? p.serve : p.serve_plus_1, // Fallback to serve+1 as proxy if null
        forehand: p.forehand,
        backhand: p.backhand,
        pace: p.defense, // Proxy pace with defensive problem solving speed
        consistency: p.consistency
    };

    const hasServeProxy = p.serve === null && p.serve_plus_1 !== null;

    const attributes = [
        { key: 'serve', label: 'Serve', value: p.serve, higherIsBetter: true, note: hasServeProxy ? 'Proxy' : undefined },
        { key: 'serve_plus_1', label: 'Serve+1', value: p.serve_plus_1, higherIsBetter: true },
        { key: 'forehand', label: 'Forehand', value: p.forehand, higherIsBetter: true },
        { key: 'backhand', label: 'Backhand', value: p.backhand, higherIsBetter: true },
        { key: 'volley_net', label: 'Volley / Net', value: p.volley_net, higherIsBetter: true },
        { key: 'defense', label: 'Defense', value: p.defense, higherIsBetter: true },
        { key: 'touch', label: 'Touch / Finesse', value: p.touch, higherIsBetter: true },
        { key: 'balance', label: 'Balance', value: p.balance, higherIsBetter: true },
        { key: 'consistency', label: 'Consistency', value: p.consistency, higherIsBetter: true },
        { key: 'physical', label: 'Physical', value: null, higherIsBetter: true, note: 'Coming soon' },
        { key: 'trick', label: 'Trick', value: null, higherIsBetter: true, note: 'Coming soon' },
        { key: 'skill_finesse', label: 'Skill/Finesse', value: null, higherIsBetter: true, note: 'Coming soon' }
    ];

    return {
        playerId,
        fullName: m?.fullName || playerId.replace(/_/g, ' '),
        lastName: m?.lastName || playerId.split('_').pop(),
        countryCode: m?.countryCode || 'US',
        imageUrl: `data/players/${playerId}/profile.png`, // Assume image exists or fallback
        elo: p.elo || '--',
        archetype: determineArchetype(p),
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
