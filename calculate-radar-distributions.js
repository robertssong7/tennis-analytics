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
    const { patterns, directionPatterns, servePlusOne } = dataPackages;

    const serveStrengthProfile = null;
    const servePlusOneAdvantage = weightedAverage(servePlusOne || []);

    const defensiveItems = (patterns || []).filter(p => isDefensive(p.shotType || p.pattern_name || p.direction || p.serveDir));
    const defensiveProblemSolving = weightedAverage(defensiveItems);

    const finishingItems = (patterns || []).filter(p => isFinishing(p.shotType || p.pattern_name));
    const finishingConversion = weightedAverage(finishingItems);

    let leftW = 0, leftN = 0, rightW = 0, rightN = 0;
    for (const p of (directionPatterns || [])) {
        const tag = directionTag(p.direction || p.shotType || p.serveDir);
        const eff = p.adjustedEffectiveness;
        const n = p.total;
        if (eff !== undefined && n !== undefined) {
            const shrunk = calculateShrunkEff(eff, n);
            if (tag === 'LEFT') { leftW += shrunk * n; leftN += n; }
            if (tag === 'RIGHT') { rightW += shrunk * n; rightN += n; }
        }
    }
    const exploitability = (leftN + rightN >= 300 && leftN > 0 && rightN > 0)
        ? Math.abs((leftW / leftN) - (rightW / rightN))
        : null;

    let patternStability = null;
    const sortedPatterns = (patterns || []).filter(p => p.total !== undefined && p.adjustedEffectiveness !== undefined)
        .sort((a, b) => b.total - a.total).slice(0, 20);

    let sumNTop20 = 0;
    for (const p of sortedPatterns) sumNTop20 += p.total;

    if (sumNTop20 >= 300 && sortedPatterns.length > 1) {
        const values = sortedPatterns.map(p => calculateShrunkEff(p.adjustedEffectiveness, p.total));
        const mean = values.reduce((a, b) => a + b, 0) / values.length;
        const trueVariance = values.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / (values.length - 1);
        patternStability = -Math.sqrt(trueVariance);
    }

    return {
        serveStrengthProfile,
        servePlusOneAdvantage,
        defensiveProblemSolving,
        finishingConversion,
        exploitability,
        patternStability
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
        exploitability: [],
        patternStability: []
    };

    for (const player of players) {
        const playerDir = path.join(DATA_DIR, 'players', player);
        const patterns = safeReadJson(path.join(playerDir, 'patterns.json'));
        const directionPatterns = safeReadJson(path.join(playerDir, 'direction-patterns.json'));
        const servePlusOne = safeReadJson(path.join(playerDir, 'serve-plus-one.json'));

        const scores = computeRawScores({ patterns, directionPatterns, servePlusOne });

        if (scores.serveStrengthProfile !== null) distributions.serveStrengthProfile.push(scores.serveStrengthProfile);
        if (scores.servePlusOneAdvantage !== null) distributions.servePlusOneAdvantage.push(scores.servePlusOneAdvantage);
        if (scores.defensiveProblemSolving !== null) distributions.defensiveProblemSolving.push(scores.defensiveProblemSolving);
        if (scores.finishingConversion !== null) distributions.finishingConversion.push(scores.finishingConversion);
        if (scores.exploitability !== null) distributions.exploitability.push(scores.exploitability);
        if (scores.patternStability !== null) distributions.patternStability.push(scores.patternStability);
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
