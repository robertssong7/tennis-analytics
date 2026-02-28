// docs/js/playerRadar/radarModel.js
import { isDefensive, isFinishing, directionTag } from './patternTaxonomy.js';

export function calculateShrunkEff(eff, n) {
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

export function computeRawScores(dataPackages) {
    const { patterns, directionPatterns, servePlusOne } = dataPackages;

    // 1. Serve Strength Profile -> Tier C (Missing underlying data)
    const serveStrengthProfile = null;

    // 2. Serve+1 Advantage -> Weighted mean over servePlusOne
    const servePlusOneAdvantage = weightedAverage(servePlusOne || []);

    // 3. Defensive Problem-Solving -> Weighted mean over patterns matches isDefensive
    const defensiveItems = (patterns || []).filter(p => isDefensive(p.shotType || p.pattern_name || p.direction || p.serveDir));
    const defensiveProblemSolving = weightedAverage(defensiveItems);

    // 4. Finishing Conversion -> Weighted mean over patterns matches isFinishing
    const finishingItems = (patterns || []).filter(p => isFinishing(p.shotType || p.pattern_name));
    const finishingConversion = weightedAverage(finishingItems);

    // 5. Exploitability (Directional Asymmetry)
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

    // 6. Pattern Stability -> Standard Deviation of adj_eff_shrunk over top 20 patterns by N
    let patternStability = null;
    const sortedPatterns = (patterns || []).filter(p => p.total !== undefined && p.adjustedEffectiveness !== undefined)
        .sort((a, b) => b.total - a.total).slice(0, 20);

    let sumNTop20 = 0;
    for (const p of sortedPatterns) sumNTop20 += p.total;

    if (sumNTop20 >= 300 && sortedPatterns.length > 1) {
        const values = sortedPatterns.map(p => calculateShrunkEff(p.adjustedEffectiveness, p.total));
        const mean = values.reduce((a, b) => a + b, 0) / values.length;
        const variance = values.reduce((a, b) => a + Math.pow(b - Math.mean, 2), 0) / values.length;
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
