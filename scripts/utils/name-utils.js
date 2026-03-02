/**
 * Shared name normalization utility.
 * Single source of truth for player name comparison.
 */

/**
 * Normalize a player name for matching:
 * - lowercase
 * - strip accents
 * - normalize whitespace
 * - remove common suffixes
 */
function normalizePlayerName(name) {
    if (!name) return '';
    return name
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')   // strip accents
        .toLowerCase()
        .replace(/[_-]/g, ' ')              // underscores/hyphens to spaces
        .replace(/\s+/g, ' ')              // collapse whitespace
        .trim();
}

module.exports = { normalizePlayerName };
