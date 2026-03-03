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
}

export function destroyHomeScreen() {
    // No cleanup needed for v2
}
