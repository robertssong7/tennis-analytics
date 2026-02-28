// docs/js/playerRadar/percentiles.js

/**
 * Computes the 0-100 percentile rank of a value against an array of reference values.
 * @param {number} value - The raw score to rank.
 * @param {number[]} distribution - Array of all baseline raw scores across the tour.
 * @param {boolean} invert - If true, lower values rank higher (e.g. Exploitability Asymmetry)
 * @returns {number} Percentile 0-100
 */
export function calculatePercentile(value, distribution, invert = false) {
    if (value === null || value === undefined) return null;
    if (!distribution || distribution.length === 0) return 50; // default if no comparison available

    // Sort distribution ascending
    const sorted = [...distribution].filter(x => x !== null && x !== undefined).sort((a, b) => a - b);
    if (sorted.length === 0) return 50;

    // Find rank
    let rank = 0;
    for (let i = 0; i < sorted.length; i++) {
        if (value > sorted[i]) {
            rank++;
        } else if (value === sorted[i]) {
            // Count ties
            let ties = 0;
            while (i + ties < sorted.length && value === sorted[i + ties]) {
                ties++;
            }
            rank += (ties / 2); // tie-breaker average
            break;
        } else {
            break;
        }
    }

    let percentile = (rank / sorted.length) * 100;

    if (invert) {
        percentile = 100 - percentile;
    }

    return Math.max(0, Math.min(100, Math.round(percentile)));
}
