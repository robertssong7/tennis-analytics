// docs/js/playerRadar/patternTaxonomy.js

/**
 * Single source of truth for pattern string classification rules.
 */

export function isDefensive(patternName) {
    if (!patternName) return false;
    const upper = patternName.toUpperCase();
    return upper.includes('SLICE') || upper.includes('LOB');
}

export function isFinishing(patternName) {
    if (!patternName) return false;
    const upper = patternName.toUpperCase();
    return upper.includes('VOLLEY') || upper.includes('SMASH');
}

export function directionTag(patternName) {
    if (!patternName) return null;
    const upper = patternName.toUpperCase();
    if (upper.includes('LEFT')) return 'LEFT';
    if (upper.includes('RIGHT')) return 'RIGHT';
    if (upper.includes('CENTER') || upper.includes('BODY') || upper.includes('T')) return 'CENTER';
    return null;
}
