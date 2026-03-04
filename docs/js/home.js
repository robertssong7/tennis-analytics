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

    // "Compare players" CTA → proxy to navbar compare button
    const compareBtn = document.getElementById('cta-compare-btn');
    if (compareBtn) {
        compareBtn.addEventListener('click', () => {
            const navBtn = document.getElementById('compare-nav-btn');
            if (navBtn) {
                navBtn.click();
            } else {
                console.error("Navigation compare button not found/loaded yet.");
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
        <div style="margin-bottom:16px;display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:12px;">
            <div>
                <div style="font-size:16px;color:#38bdf8;text-transform:uppercase;letter-spacing:1px;font-weight:800;margin-bottom:6px;">Featured Player</div>
                <div style="font-size:24px;font-weight:900;color:#0f172a;">${displayName}</div>
            </div>
            <div id="home-featured-surface-toggle-mount" style="background:#fff; border-radius:12px; padding:4px; box-shadow:0 4px 12px rgba(0,0,0,0.05);"></div>
        </div>
        <div id="home-featured-matchups"></div>
    `;

    try {
        const { createSurfaceToggle } = await import('./SurfaceToggle.js?v=home2');

        createSurfaceToggle('home-featured-surface-toggle-mount', async (newSurface) => {
            const surfKey = newSurface === 'all' ? 'All' : newSurface.charAt(0).toUpperCase() + newSurface.slice(1).toLowerCase();
            window._homeMatchupSurface = surfKey;

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
    } catch (e) {
        console.error("Failed to load Surface Toggle for home:", e);
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
