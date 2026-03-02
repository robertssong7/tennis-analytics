/* ═══════════════════════════════════════════════════════════════════════
   TennisIQ — Home Screen Logic
   Live scores panel, tournament spotlight, data panel slots
   ═══════════════════════════════════════════════════════════════════════ */

const ENABLE_HOME_LIVE = true;

const LIVE_SCORES_URL = 'data/live-scores.json';
const POLL_INTERVAL_MS = 90_000;
const STALE_THRESHOLD_MS = 10 * 60 * 1000; // 10 minutes

let pollTimer = null;

// ─── Init ────────────────────────────────────────────────────────────
export function initHomeScreen() {
    if (ENABLE_HOME_LIVE) {
        fetchAndRenderLive();
        pollTimer = setInterval(fetchAndRenderLive, POLL_INTERVAL_MS);
    }
}

export function destroyHomeScreen() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}

// ─── Live Scores Fetch ───────────────────────────────────────────────
async function fetchAndRenderLive() {
    const panel = document.getElementById('home-live-panel');
    const spotlightSection = document.getElementById('home-spotlight');
    if (!panel) return;

    try {
        const resp = await fetch(LIVE_SCORES_URL);
        if (!resp.ok) throw new Error('fetch failed');
        const data = await resp.json();

        // Stale check
        const fetchedAt = new Date(data.fetchedAt).getTime();
        if (Date.now() - fetchedAt > STALE_THRESHOLD_MS) {
            hidePanel(panel);
            hidePanel(spotlightSection);
            return;
        }

        // Empty check
        const matches = data.matches || [];
        if (matches.length === 0) {
            hidePanel(panel);
            hidePanel(spotlightSection);
            return;
        }

        renderLivePanel(panel, matches);
        renderSpotlight(spotlightSection, data.featuredTournament, matches);

    } catch {
        // On failure: hide panel silently
        hidePanel(panel);
        hidePanel(spotlightSection);
    }
}

function hidePanel(el) {
    if (el) el.style.display = 'none';
}

// ─── Render Live Scores Panel (Hero Right Column) ────────────────────
function renderLivePanel(panel, matches) {
    // Sort: live first, then by round descending
    const roundOrder = { 'F': 0, 'SF': 1, 'QF': 2, 'R16': 3, 'R32': 4, 'R64': 5, 'R128': 6 };
    const sorted = [...matches]
        .filter(m => m.status === 'live' || m.status === 'completed')
        .sort((a, b) => {
            if (a.status === 'live' && b.status !== 'live') return -1;
            if (b.status === 'live' && a.status !== 'live') return 1;
            return (roundOrder[a.round] ?? 99) - (roundOrder[b.round] ?? 99);
        })
        .slice(0, 5);

    if (sorted.length === 0) {
        hidePanel(panel);
        return;
    }

    const hasLive = sorted.some(m => m.status === 'live');

    panel.innerHTML = `
        <div class="home-live-header">
            <div class="home-live-badge">
                ${hasLive ? '<div class="home-live-dot"></div>' : ''}
                ${hasLive ? 'Live Scores' : 'Recent Results'}
            </div>
            <div class="home-live-tournament">${sorted[0].tournamentName || ''}</div>
        </div>
        <div class="home-live-matches">
            ${sorted.map(m => renderMatchRow(m)).join('')}
        </div>
    `;

    panel.style.display = 'block';
    // Fade in
    requestAnimationFrame(() => panel.classList.add('visible'));
}

function renderMatchRow(m) {
    const isLive = m.status === 'live';
    const sets = m.setScores || [];
    const gameScore = m.currentGameScore || '';

    return `
        <div class="home-match-row">
            <div class="home-match-players">
                <div class="home-match-player">
                    ${isLive && m.serving === 1 ? '<div class="home-match-serving-dot"></div>' : '<div style="width:6px"></div>'}
                    ${m.player1Name || 'TBD'}
                </div>
                <div class="home-match-player">
                    ${isLive && m.serving === 2 ? '<div class="home-match-serving-dot"></div>' : '<div style="width:6px"></div>'}
                    ${m.player2Name || 'TBD'}
                </div>
            </div>
            <div class="home-match-scores">
                ${sets.map(s => `
                    <div style="display:flex;flex-direction:column;gap:2px;text-align:center;">
                        <span class="home-match-set">${s.p1}</span>
                        <span class="home-match-set">${s.p2}</span>
                    </div>
                `).join('')}
                ${isLive && gameScore ? `
                    <div style="display:flex;flex-direction:column;gap:2px;text-align:center;">
                        <span class="home-match-set current-game">${gameScore.split('-')[0] || ''}</span>
                        <span class="home-match-set current-game">${gameScore.split('-')[1] || ''}</span>
                    </div>
                ` : ''}
                ${isLive ? '<div class="home-match-live-indicator"></div>' : ''}
            </div>
        </div>
    `;
}

// ─── Render Tournament Spotlight (Zone 2) ────────────────────────────
function renderSpotlight(section, tournament, matches) {
    if (!section || !tournament) {
        hidePanel(section);
        return;
    }

    const spotlightMatches = matches
        .filter(m => m.status === 'live')
        .slice(0, 3);

    if (spotlightMatches.length === 0) {
        hidePanel(section);
        return;
    }

    const infoEl = section.querySelector('.home-spotlight-info');
    const matchesEl = section.querySelector('.home-spotlight-matches');

    if (infoEl) {
        infoEl.innerHTML = `
            <div class="home-spotlight-name">${tournament.name || 'Tournament'}</div>
            <div class="home-spotlight-meta">
                ${tournament.surface ? `<span>${tournament.surface}</span>` : ''}
                ${tournament.round ? `<span>${tournament.round}</span>` : ''}
            </div>
        `;
    }

    if (matchesEl) {
        matchesEl.innerHTML = spotlightMatches.map(m => `
            <div class="home-spotlight-match">
                <span>${m.player1Name || 'TBD'} vs ${m.player2Name || 'TBD'}</span>
                <span style="color:var(--home-accent);font-weight:600;">
                    ${(m.setScores || []).map(s => `${s.p1}-${s.p2}`).join(' ')}
                </span>
            </div>
        `).join('');
    }

    section.style.display = 'block';
}
