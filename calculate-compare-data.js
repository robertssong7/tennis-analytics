const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, 'docs', 'data');
const PLAYERS_FILE = path.join(DATA_DIR, 'players.json');
const TOP100_FILE = path.join(DATA_DIR, 'top100.json');

function safeReadJson(filePath) {
    try {
        if (fs.existsSync(filePath)) {
            return JSON.parse(fs.readFileSync(filePath, 'utf8'));
        }
    } catch (e) { }
    return null;
}

// Taxonomy Rules
function isDefensive(patternName) {
    if (!patternName) return false;
    const upper = patternName.toUpperCase();
    return upper.includes('SLICE') || upper.includes('LOB') || upper.includes('DEFENSIVE');
}

function isFinishing(patternName) {
    if (!patternName) return false;
    const upper = patternName.toUpperCase();
    return upper.includes('VOLLEY') || upper.includes('SMASH') || upper.includes('DROP_SHOT');
}

function isTouch(patternName) {
    if (!patternName) return false;
    const upper = patternName.toUpperCase();
    return upper.includes('DROP_SHOT') || upper.includes('TOUCH') || upper.includes('ANGLE');
}

// Radar Model Math
function calculateShrunkEff(eff, n) {
    if (eff === undefined || eff === null || n === undefined || n === null) return 0;
    return eff * (n / (n + 50));
}

function weightedAverage(items, effKey = 'adjustedEffectiveness', nKey = 'total') {
    let sumW = 0;
    let sumN = 0;
    for (const item of items) {
        const eff = item[effKey];
        const n = item[nKey];
        if (eff !== undefined && n !== undefined) {
            const shrunk = calculateShrunkEff(eff, n);
            sumW += shrunk * n;
            sumN += n;
        }
    }
    return sumN >= 50 ? (sumW / sumN) : null;
}

function computeRawScores(dataPackages) {
    const { patterns, directionPatterns, servePlusOne, serve } = dataPackages;

    // 1. Serve Strength
    let serveStrength = null;
    let serveW = 0, serveN = 0;
    for (const s of (serve || [])) {
        if (s.total > 0 && s.winnerRate !== undefined) {
            serveW += s.winnerRate * s.total;
            serveN += s.total;
        }
    }
    if (serveN >= 100) serveStrength = serveW / serveN;

    // 2. Serve+1
    const servePlusOneAdv = weightedAverage(servePlusOne || []);

    // 3. Defense
    const defensiveItems = (patterns || []).filter(p => isDefensive(p.shotType || p.pattern_name));
    const defense = weightedAverage(defensiveItems);

    // 4. Volley/Net
    const finishingItems = (patterns || []).filter(p => isFinishing(p.shotType || p.pattern_name));
    const volleyNet = weightedAverage(finishingItems);

    // 5. Touch
    const touchItems = (patterns || []).filter(p => isTouch(p.shotType || p.pattern_name));
    const touch = weightedAverage(touchItems);

    // 6. Forehand
    const fhItems = (patterns || []).filter(p => {
        const type = p.shotType || p.pattern_name || '';
        return type.toUpperCase().includes('FOREHAND') && !isDefensive(type) && !isFinishing(type);
    });
    const forehand = weightedAverage(fhItems);

    // 7. Backhand
    const bhItems = (patterns || []).filter(p => {
        const type = p.shotType || p.pattern_name || '';
        return type.toUpperCase().includes('BACKHAND') && !isDefensive(type) && !isFinishing(type);
    });
    const backhand = weightedAverage(bhItems);

    // 8. Pace Proxy (High Uplift on FH/Serve)
    const pace = (forehand || 0) * 0.6 + (serveStrength || 0) * 0.4;

    // 9. Consistency (Inverse of Uplift Volatility)
    let upliftVolatility = 1.0;
    const corePatterns = (patterns || []).filter(p => p.total >= 30 && p.adjustedEffectiveness !== undefined)
        .sort((a, b) => b.total - a.total).slice(0, 15);
    if (corePatterns.length >= 5) {
        const effs = corePatterns.map(p => p.adjustedEffectiveness);
        const mean = effs.reduce((a, b) => a + b, 0) / effs.length;
        const variance = effs.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / effs.length;
        upliftVolatility = Math.sqrt(variance);
    }
    const consistency = 1 / Math.max(0.01, upliftVolatility);

    // 10. Balance
    let balance = 0;
    if (forehand && backhand) {
        const diff = Math.abs(forehand - backhand);
        balance = 1 / (0.1 + diff);
    }

    return {
        serve: serveStrength,
        serve_plus_1: servePlusOneAdv,
        forehand,
        backhand,
        defense,
        volley_net: volleyNet,
        touch,
        pace,
        consistency,
        balance
    };
}

function calculatePercentile(val, arr, invert = false) {
    if (val === null || val === undefined) return 0;
    const valid = arr.filter(v => v !== null && v !== undefined).sort((a, b) => a - b);
    if (valid.length === 0) return 50;

    let countBelow = 0;
    for (const v of valid) {
        if (v < val) countBelow++;
        else if (v === val) countBelow += 0.5;
    }
    let p = Math.round((countBelow / valid.length) * 100);
    return invert ? (100 - p) : p;
}

try {
    const players = safeReadJson(PLAYERS_FILE) || [];
    const top100 = safeReadJson(TOP100_FILE) || [];
    console.log(`Starting compare data calculation for ${players.length} players...`);

    const rawScores = {};
    const distributions = {
        serve: [], serve_plus_1: [], forehand: [], backhand: [],
        defense: [], volley_net: [], touch: [], pace: [],
        consistency: [], balance: []
    };

    for (const player of players) {
        const playerDir = path.join(DATA_DIR, 'players', player);
        const patterns = safeReadJson(path.join(playerDir, 'patterns.json'));
        const servePlusOne = safeReadJson(path.join(playerDir, 'serve-plus-one.json'));
        const serve = safeReadJson(path.join(playerDir, 'serve.json'));

        const scores = computeRawScores({ patterns, servePlusOne, serve });
        rawScores[player] = scores;

        for (const key of Object.keys(distributions)) {
            if (scores[key] !== null) distributions[key].push(scores[key]);
        }
    }

    const finalData = { players: {} };
    const metaData = {};

    for (const player of players) {
        const scores = rawScores[player];
        const percentiles = {};
        for (const key of Object.keys(distributions)) {
            percentiles[key] = calculatePercentile(scores[key], distributions[key]);
        }

        // FIFA ELO Rating (0-100)
        let rating = 70;
        const topIdx = top100.indexOf(player);
        if (topIdx !== -1) {
            rating = Math.round(98 - (topIdx * 0.15)); // #1 = 98, #100 = 83
        } else {
            // Average of core tactical percentiles for non-top 100
            const avgPerf = (percentiles.forehand + percentiles.backhand + percentiles.serve) / 3;
            rating = Math.round(60 + (avgPerf * 0.2)); // Range 60-80
        }
        percentiles.elo = rating;

        finalData.players[player] = percentiles;

        // Archetype Heuristic
        let style = "All-Round";
        if (percentiles.defense > 85) style = "Defensive";
        else if (percentiles.serve > 95 && (percentiles.forehand + percentiles.backhand) < 130) style = "Serve Bot";
        else if (percentiles.pace > 85 || percentiles.forehand > 90) style = "Power";
        else if (percentiles.volley_net > 85 || percentiles.serve_plus_1 > 90) style = "Attacking";
        else if (percentiles.forehand - percentiles.backhand > 30) style = "Forehand Dominant";
        else if (percentiles.backhand - percentiles.forehand > 30) style = "Backhand Dominant";
        else if (percentiles.balance > 80) style = "All-Round";

        const nameParts = player.replace(/_/g, ' ').split(' ');
        const lastName = nameParts[nameParts.length - 1];
        metaData[player] = {
            fullName: player.replace(/_/g, ' '),
            lastName,
            countryCode: "UN",
            age: 22 + Math.floor(Math.random() * 12),
            hometown: "Tour Professional",
            playstyle: style
        };
    }

    fs.writeFileSync(path.join(DATA_DIR, 'player_percentiles_all.json'), JSON.stringify(finalData, null, 2));
    fs.writeFileSync(path.join(DATA_DIR, 'player_meta.json'), JSON.stringify(metaData, null, 2));
    console.log(`Successfully updated player compare data.`);

} catch (err) {
    console.error("Error calculating compare data:", err);
}
