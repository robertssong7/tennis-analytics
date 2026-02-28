const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, 'docs', 'data');
const PLAYERS_FILE = path.join(DATA_DIR, 'players.json');

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
    return upper.includes('VOLLEY') || upper.includes('SMASH');
}

function directionTag(patternName) {
    if (!patternName) return null;
    const upper = patternName.toUpperCase();
    if (upper.includes('LEFT')) return 'LEFT';
    if (upper.includes('RIGHT')) return 'RIGHT';
    if (upper.includes('CENTER') || upper.includes('BODY') || upper.includes('T')) return 'CENTER';
    return null;
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
    return sumN >= 300 ? (sumW / sumN) : null;
}

function computeRawScores(dataPackages) {
    const { patterns, directionPatterns, servePlusOne, serve } = dataPackages;

    let serveStrengthProfile = null;
    let serveW = 0, serveN = 0;
    for (const s of (serve || [])) {
        if (s.total > 0 && s.winnerRate !== undefined) {
            serveW += s.winnerRate * s.total;
            serveN += s.total;
        }
    }
    if (serveN >= 100) serveStrengthProfile = serveW / serveN;

    const servePlusOneAdvantage = weightedAverage(servePlusOne || []);

    const defensiveItems = (patterns || []).filter(p => isDefensive(p.shotType || p.pattern_name || p.direction || p.serveDir));
    const defensiveProblemSolving = weightedAverage(defensiveItems);

    const finishingItems = (patterns || []).filter(p => isFinishing(p.shotType || p.pattern_name));
    const finishingConversion = weightedAverage(finishingItems);

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

    return {
        serveStrengthProfile,
        servePlusOneAdvantage,
        defensiveProblemSolving,
        finishingConversion,
        exploitability
    };
}

try {
    const players = JSON.parse(fs.readFileSync(PLAYERS_FILE, 'utf8'));
    console.log(`Starting radar distribution calculation for ${players.length} players...`);

    const distributions = {
        serveStrengthProfile: [],
        servePlusOneAdvantage: [],
        defensiveProblemSolving: [],
        finishingConversion: [],
        exploitability: []
    };

    for (const player of players) {
        const playerDir = path.join(DATA_DIR, 'players', player);
        const patterns = safeReadJson(path.join(playerDir, 'patterns.json'));
        const directionPatterns = safeReadJson(path.join(playerDir, 'direction-patterns.json'));
        const servePlusOne = safeReadJson(path.join(playerDir, 'serve-plus-one.json'));
        const serve = safeReadJson(path.join(playerDir, 'serve.json'));

        const scores = computeRawScores({ patterns, directionPatterns, servePlusOne, serve });

        if (scores.serveStrengthProfile !== null) distributions.serveStrengthProfile.push(scores.serveStrengthProfile);
        if (scores.servePlusOneAdvantage !== null) distributions.servePlusOneAdvantage.push(scores.servePlusOneAdvantage);
        if (scores.defensiveProblemSolving !== null) distributions.defensiveProblemSolving.push(scores.defensiveProblemSolving);
        if (scores.finishingConversion !== null) distributions.finishingConversion.push(scores.finishingConversion);
        if (scores.exploitability !== null) distributions.exploitability.push(scores.exploitability);
    }

    const outPath = path.join(DATA_DIR, 'tourDistributionsAll.json');
    fs.writeFileSync(outPath, JSON.stringify(distributions, null, 2));

    for (const key of Object.keys(distributions)) {
        console.log(` - ${key}: ${distributions[key].length} valid players`);
    }
    console.log(`\nSuccessfully wrote distributions to ${outPath}`);

} catch (err) {
    console.error("Error calculating radar distributions:", err);
}
