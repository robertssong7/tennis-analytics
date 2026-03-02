/**
 * Shared score utilities for classifying point score states.
 * Used by migrate-score-state.js and could be reused by API routes.
 */

/**
 * Parse a game score string like '40-0', '15-30', 'AD-40', 'DEUCE'
 * Returns { serverPts, receiverPts } as numeric values (0-4+)
 */
function parseGameScore(scoreStr) {
    if (!scoreStr) return null;
    const s = scoreStr.trim().toUpperCase();
    if (s === 'DEUCE') return { serverPts: 3, receiverPts: 3 };
    const parts = s.split('-');
    if (parts.length !== 2) return null;
    const map = { '0': 0, '15': 1, '30': 2, '40': 3, 'AD': 4 };
    const sp = map[parts[0]];
    const rp = map[parts[1]];
    if (sp === undefined || rp === undefined) return null;
    return { serverPts: sp, receiverPts: rp };
}

/**
 * Classify a point into one of the 10 score states.
 * 
 * @param {string} gameScore - e.g. '40-0' (server perspective)
 * @param {number} serverGames - games won by server in current set
 * @param {number} receiverGames - games won by receiver in current set
 * @param {boolean} isPlayerServer - whether the target player is serving
 * @returns {string} score_state_label
 */
function classifyScoreState(gameScore, serverGames, receiverGames, isPlayerServer) {
    const score = parseGameScore(gameScore);
    if (!score) return 'neutral';

    const { serverPts, receiverPts } = score;

    // Determine if it's a break point (from server's perspective: receiver has BP)
    const isBreakPoint = (
        (serverPts <= 2 && receiverPts === 3) ||   // 0-40, 15-40, 30-40
        (serverPts === 3 && receiverPts === 4)      // 40-AD
    );

    if (isBreakPoint) {
        if (isPlayerServer) return 'break_point_save';      // Player is defending serve
        else return 'break_point_convert';                   // Player is trying to break
    }

    // Set-level pressure
    const sg = serverGames || 0;
    const rg = receiverGames || 0;
    const pGames = isPlayerServer ? sg : rg;
    const oGames = isPlayerServer ? rg : sg;

    // Close games in set (5-3, 5-4, 6-5 either way)
    const isCloseSet = (
        (pGames >= 5 && oGames >= 3 && pGames - oGames <= 2) ||
        (oGames >= 5 && pGames >= 3 && oGames - pGames <= 2)
    );

    if (isCloseSet) {
        const ahead = pGames > oGames;
        const behind = pGames < oGames;
        if (isPlayerServer && ahead) return 'set_serving_ahead';
        if (isPlayerServer && behind) return 'set_serving_behind';
        if (!isPlayerServer && ahead) return 'set_returning_ahead';
        if (!isPlayerServer && behind) return 'set_returning_behind';
    }

    // Deuce pressure (30-30 or deuce, not already a break point)
    if ((serverPts === 2 && receiverPts === 2) ||
        (serverPts === 3 && receiverPts === 3)) {
        return 'deuce_pressure';
    }

    // Behind in game (from player's perspective)
    const playerPts = isPlayerServer ? serverPts : receiverPts;
    const oppPts = isPlayerServer ? receiverPts : serverPts;

    if ((playerPts === 0 && oppPts >= 2) ||   // 0-30, 0-40
        (playerPts === 1 && oppPts === 3)) {   // 15-40
        return 'behind_in_game';
    }

    // Ahead in game
    if ((oppPts === 0 && playerPts >= 2) ||    // 30-0, 40-0
        (oppPts === 1 && playerPts === 3)) {    // 40-15
        return 'ahead_in_game';
    }

    return 'neutral';
}

module.exports = { parseGameScore, classifyScoreState };
