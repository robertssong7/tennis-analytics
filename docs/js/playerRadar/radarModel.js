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
    const { patterns, directionPatterns, servePlusOne, serve } = dataPackages;

    // 1. Serve Strength Profile -> Tier C Fallback: Overall conversion vs Baseline
    // Ideally ESPW, but for now we aggregate the provided serve data
    let serveStrengthProfile = null;
    let serveW = 0, serveN = 0;
    for (const s of (serve || [])) {
        if (s.total > 0 && s.winnerRate !== undefined) {
            serveW += s.winnerRate * s.total;
            serveN += s.total;
        }
    }
    if (serveN >= 100) serveStrengthProfile = serveW / serveN;

    // 2. Serve+1 Advantage -> Weighted mean over servePlusOne
    const servePlusOneAdvantage = weightedAverage(servePlusOne || []);

    // 3. Defensive Problem-Solving -> Weighted mean over patterns matches isDefensive
    const defensiveItems = (patterns || []).filter(p => isDefensive(p.shotType || p.pattern_name || p.direction || p.serveDir));
    const defensiveProblemSolving = weightedAverage(defensiveItems);

    // 4. Finishing Conversion -> Weighted mean over patterns matches isFinishing
    const finishingItems = (patterns || []).filter(p => isFinishing(p.shotType || p.pattern_name));
    const finishingConversion = weightedAverage(finishingItems);

    // 5. Exploitability -> Shot Balance (Variability across main shot types)
    // Calculate standard deviation of adjustedEffectiveness across Core Shots
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
