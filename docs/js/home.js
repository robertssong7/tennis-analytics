/* ═══════════════════════════════════════════════════════════════════════
   TennisIQ — Home Screen Logic v2
   CTA button handlers, smooth scroll
   ═══════════════════════════════════════════════════════════════════════ */

export function initHomeScreen() {
    // "Search a player" CTA → focus search input
    const searchBtn = document.getElementById('cta-search-btn');
    if (searchBtn) {
        searchBtn.addEventListener('click', () => {
            const input = document.getElementById('player-search');
            if (input) {
                input.focus();
                input.select();
            }
        });
    }

    // "Compare players" CTA → open compare modal
    const compareBtn = document.getElementById('cta-compare-btn');
    if (compareBtn) {
        compareBtn.addEventListener('click', async () => {
            try {
                const { mountCompareFeature, openModal } = await import('./js/playerCompare/index.js?v=srf2');
                await mountCompareFeature();
                openModal();
            } catch (err) {
                console.error('Failed to open compare modal from home:', err);
            }
        });
    }

    // Smooth scroll for "See how it works" link
    const tertiaryLink = document.querySelector('.cta-tertiary');
    if (tertiaryLink) {
        tertiaryLink.addEventListener('click', (e) => {
            e.preventDefault();
            const target = document.getElementById('how-it-works');
            if (target) {
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        });
    }

    // ─── Render Featured Matchups in Hero Right Panel ───
    renderHomeMatchups();
}

async function renderHomeMatchups() {
    const container = document.getElementById('matchup-explorer-home');
    if (!container) return;

    let featuredPlayer = 'carlos_alcaraz'; // Default
    try {
        const stored = localStorage.getItem('tennisIQ_lastPlayer');
        if (stored) featuredPlayer = stored;
    } catch (e) { }

    const displayName = featuredPlayer.replace(/_/g, ' ').replace(/\w\S*/g, w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase());

    // Header for the widget
    container.innerHTML = `
        <div style="margin-bottom:12px;display:flex;justify-content:space-between;align-items:flex-end;">
            <div>
                <div style="font-size:11px;color:#38bdf8;text-transform:uppercase;letter-spacing:1px;font-weight:700;margin-bottom:4px;">Featured Player</div>
                <div style="font-size:18px;font-weight:800;color:#f8fafc;">${displayName}</div>
            </div>
            <div style="font-size:12px;color:#64748b;">All Surfaces</div>
        </div>
        <div id="home-featured-matchups"></div>
    `;

    try {
        const { renderMatchupExplorer, injectMatchupStyles } = await import('./matchups/MatchupExplorer.js');
        injectMatchupStyles();
        await renderMatchupExplorer('home-featured-matchups', featuredPlayer, 'All');
    } catch (err) {
        console.error('Failed to render home matchups:', err);
    }
}

export function destroyHomeScreen() {
    // No cleanup needed for v2
}
