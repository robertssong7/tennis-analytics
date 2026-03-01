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
    return upper.includes('SLICE') || upper.includes('LOB');
}

function isFinishing(patternName) {
    if (!patternName) return false;
    const upper = patternName.toUpperCase();
    return upper.includes('VOLLEY') || upper.includes('SMASH') || upper.includes('DROP_SHOT');
}

function isTouch(patternName) {
    if (!patternName) return false;
    const upper = patternName.toUpperCase();
    return upper.includes('DROP_SHOT') || upper.includes('SLICE') || upper.includes('TOUCH');
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

    let serveStrength = null;
    let serveW = 0, serveN = 0;
    for (const s of (serve || [])) {
        if (s.total > 0 && s.winnerRate !== undefined) {
            serveW += s.winnerRate * s.total;
            serveN += s.total;
        }
    }
    if (serveN >= 100) serveStrength = serveW / serveN;

    const servePlusOneAdv = weightedAverage(servePlusOne || []);

    const defensiveItems = (patterns || []).filter(p => isDefensive(p.shotType || p.pattern_name || p.direction || p.serveDir));
    const defense = weightedAverage(defensiveItems);

    const finishingItems = (patterns || []).filter(p => isFinishing(p.shotType || p.pattern_name));
    const volleyNet = weightedAverage(finishingItems);

    const touchItems = (patterns || []).filter(p => isTouch(p.shotType || p.pattern_name));
    const touch = weightedAverage(touchItems);

    const fhItems = (patterns || []).filter(p => {
        const type = p.shotType || p.pattern_name || '';
        return type.toUpperCase().includes('FOREHAND') && !isDefensive(type) && !isFinishing(type);
    });
    const forehand = weightedAverage(fhItems);

    const bhItems = (patterns || []).filter(p => {
        const type = p.shotType || p.pattern_name || '';
        return type.toUpperCase().includes('BACKHAND') && !isDefensive(type) && !isFinishing(type);
    });
    const backhand = weightedAverage(bhItems);

    let exploitability = null;
    const coreShotTypes = ['FOREHAND', 'BACKHAND', 'FOREHAND_VOLLEY', 'BACKHAND_VOLLEY', 'OVERHEAD', 'DROP_SHOT'];
    const coreShots = (patterns || []).filter(p => p.shotType && coreShotTypes.includes(p.shotType.toUpperCase()) && p.total >= 10 && p.adjustedEffectiveness !== undefined);

    let sumW = 0, sumN = 0;
    for (const p of coreShots) {
        sumW += p.adjustedEffectiveness * p.total;
        sumN += p.total;
    }

    if (sumN >= 50 && coreShots.length >= 2) {
        const mean = sumW / sumN;
        let varianceSum = 0;
        for (const p of coreShots) {
            varianceSum += p.total * Math.pow(p.adjustedEffectiveness - mean, 2);
        }
        exploitability = Math.sqrt(varianceSum / sumN);
    }

    // Consistency is roughly inverse of variance/exploitability, but we also can use error rate. 
    // We'll use 1 / exploitability as a rough proxy for consistency if exploitability exists
    const consistency = exploitability !== null ? (1 / Math.max(0.01, exploitability)) : null;

    return {
        serve: serveStrength,
        serve_plus_1: servePlusOneAdv,
        forehand,
        backhand,
        defense,
        volley_net: volleyNet,
        touch,
        balance_raw: exploitability, // Balance is inverted exploitability later
        consistency_raw: consistency
    };
}

function calculatePercentile(val, arr, invert = false) {
    if (val === null || val === undefined) return null;
    const valid = arr.filter(v => v !== null && v !== undefined).sort((a, b) => a - b);
    if (valid.length === 0) return null;

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
    console.log(`Starting compare data calculation for ${players.length} players...`);

    const rawScores = {};
    const distributions = {
        serve: [],
        serve_plus_1: [],
        forehand: [],
        backhand: [],
        defense: [],
        volley_net: [],
        touch: [],
        balance_raw: [],
        consistency_raw: []
    };

    for (const player of players) {
        const playerDir = path.join(DATA_DIR, 'players', player);
        const patterns = safeReadJson(path.join(playerDir, 'patterns.json'));
        const directionPatterns = safeReadJson(path.join(playerDir, 'direction-patterns.json'));
        const servePlusOne = safeReadJson(path.join(playerDir, 'serve-plus-one.json'));
        const serve = safeReadJson(path.join(playerDir, 'serve.json'));

        const scores = computeRawScores({ patterns, directionPatterns, servePlusOne, serve });
        rawScores[player] = scores;

        for (const key of Object.keys(distributions)) {
            if (scores[key] !== null) {
                distributions[key].push(scores[key]);
            }
        }
    }

    const finalData = { players: {} };
    const metaData = {};

    let fakeEloBase = 1500;

    for (const player of players) {
        const scores = rawScores[player];

        // Derived percentiles
        const percentiles = {
            elo: fakeEloBase + Math.floor(Math.random() * 500), // Placeholder ELO
            serve: calculatePercentile(scores.serve, distributions.serve),
            serve_plus_1: calculatePercentile(scores.serve_plus_1, distributions.serve_plus_1),
            forehand: calculatePercentile(scores.forehand, distributions.forehand),
            backhand: calculatePercentile(scores.backhand, distributions.backhand),
            defense: calculatePercentile(scores.defense, distributions.defense),
            volley_net: calculatePercentile(scores.volley_net, distributions.volley_net),
            touch: calculatePercentile(scores.touch, distributions.touch),
            balance: calculatePercentile(scores.balance_raw, distributions.balance_raw, true), // Inverted! lower expl = higher balance
            consistency: calculatePercentile(scores.consistency_raw, distributions.consistency_raw)
        };

        finalData.players[player] = percentiles;

        // Meta Data
        const nameParts = player.replace(/_/g, ' ').split(' ');
        const lastName = nameParts[nameParts.length - 1];
        const firstName = nameParts.slice(0, nameParts.length - 1).join(' ');

        metaData[player] = {
            fullName: player.replace(/_/g, ' '),
            lastName,
            countryCode: "US", // Default stub
            age: 25 + Math.floor(Math.random() * 10),
            hometown: "Unknown",
            playstyle: "All-Round",
            racket: "Custom"
        };
    }

    fs.writeFileSync(path.join(DATA_DIR, 'player_percentiles_all.json'), JSON.stringify(finalData, null, 2));
    fs.writeFileSync(path.join(DATA_DIR, 'player_meta.json'), JSON.stringify(metaData, null, 2));

    console.log(`Successfully wrote player_percentiles_all.json and player_meta.json`);

} catch (err) {
    console.error("Error calculating compare data:", err);
}
