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

    // Render the initial surface state
    window._homeMatchupSurface = window._homeMatchupSurface || 'All';

    // Header for the widget
    container.innerHTML = `
        <div style="margin-bottom:16px;display:flex;justify-content:space-between;align-items:flex-end;">
            <div>
                <div style="font-size:16px;color:#38bdf8;text-transform:uppercase;letter-spacing:1px;font-weight:800;margin-bottom:6px;">Featured Player</div>
                <div style="font-size:24px;font-weight:900;color:#0f172a;">${displayName}</div>
            </div>
            <div>
                <select id="home-featured-surface-select" style="padding:6px 12px; border-radius:8px; border:1px solid #cbd5e1; font-size:14px; color:#334155; font-weight:600; cursor:pointer; background:#fff;">
                    <option value="All" ${window._homeMatchupSurface === 'All' ? 'selected' : ''}>All Surfaces</option>
                    <option value="Hard" ${window._homeMatchupSurface === 'Hard' ? 'selected' : ''}>Hard</option>
                    <option value="Clay" ${window._homeMatchupSurface === 'Clay' ? 'selected' : ''}>Clay</option>
                    <option value="Grass" ${window._homeMatchupSurface === 'Grass' ? 'selected' : ''}>Grass</option>
                </select>
            </div>
        </div>
        <div id="home-featured-matchups"></div>
    `;

    // Bind event listener to the dropdown
    const surfaceSelect = document.getElementById('home-featured-surface-select');
    if (surfaceSelect) {
        surfaceSelect.addEventListener('change', async (e) => {
            window._homeMatchupSurface = e.target.value;
            const target = document.getElementById('home-featured-matchups');
            if (target) {
                target.innerHTML = '<div style="padding: 20px; text-align: center; color: #64748b;">Loading matchups...</div>';
                try {
                    const { renderMatchupExplorer } = await import('./matchups/MatchupExplorer.js');
                    await renderMatchupExplorer('home-featured-matchups', featuredPlayer, window._homeMatchupSurface, true);
                } catch (err) {
                    console.error(err);
                }
            }
        });
    }

    try {
        const { renderMatchupExplorer, injectMatchupStyles } = await import('./matchups/MatchupExplorer.js');
        injectMatchupStyles();
        // pass a true flag to indicate it's the home screen (so we limit to top 3 and add UX refinements)
        await renderMatchupExplorer('home-featured-matchups', featuredPlayer, window._homeMatchupSurface, true);
    } catch (err) {
        console.error('Failed to render home matchups:', err);
    }
}

export function destroyHomeScreen() {
    // No cleanup needed for v2
}
