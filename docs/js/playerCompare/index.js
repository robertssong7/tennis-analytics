import { initCompareData, getPlayerList, buildCompareProfile, setCompareSurface, fetchH2H } from './playerCompareModel.js';
import { renderCompareCard } from './PlayerCompareCard.js';
import { renderCompareRadar } from './CompareRadarPentagon.js';
import { renderCompareBars } from './PlayerCompareBars.js';
import { createSurfaceToggle } from '../SurfaceToggle.js';

let modalRoot = null;
let playerAId = null;
let playerBId = null;
let compareSurfaceToggle = null;
let currentH2HData = null;

// CSS Injected Once
const STYLES = `
.compare-modal-overlay {
    position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
    background: rgba(11, 17, 32, 0.95); backdrop-filter: blur(8px);
    z-index: 9999; display: none; align-items: flex-start; justify-content: center;
    overflow-y: auto; padding: 40px 20px;
}
.compare-modal-content {
    background: #0f172a; width: 100%; max-width: 1000px; border-radius: 12px;
    border: 1px solid #334155; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5);
    display: flex; flex-direction: column; overflow: hidden;
}
.compare-modal-header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 20px 24px; border-bottom: 1px solid #1e293b;
}
.compare-modal-title { margin: 0; font-size: 20px; font-weight: 700; color: #f8fafc; }
.compare-modal-subtitle { font-size: 13px; color: #94a3b8; font-weight: normal; margin-left: 12px; }
.compare-modal-close { background: transparent; border: none; color: #94a3b8; font-size: 28px; cursor: pointer; line-height: 1; }
.compare-modal-close:hover { color: #f8fafc; }

.compare-top-row {
    display: grid; grid-template-columns: 1fr 300px 1fr; gap: 24px; padding: 24px; align-items: center;
}
.search-compare-input {
    width: 100%; background: #1e293b; border: 1px solid #334155; color: #f8fafc;
    padding: 10px 16px; border-radius: 6px; margin-bottom: 16px;
}
.typeahead-results {
    position: absolute; background: #1e293b; border: 1px solid #334155; 
    border-radius: 6px; width: calc(100% - 32px); max-height: 200px;
    overflow-y: auto; z-index: 1000; display: none; margin-top: -12px;
}
.typeahead-results.left { left: 16px; }
.typeahead-results.right { right: 16px; width: 100%; max-width: calc(100% - 32px); }
.typeahead-item { padding: 10px 16px; cursor: pointer; color: #cbd5e1; font-size: 14px; }
.typeahead-item:hover { background: #334155; color: #f8fafc; }

.compare-radar-container { height: 280px; position: relative; }
.swap-btn {
    background: #1e293b; border: 1px solid #334155; color: #cbd5e1;
    border-radius: 20px; padding: 6px 16px; cursor: pointer; font-size: 13px;
    font-weight: 600; display: block; margin: -10px auto 10px auto;
}
.swap-btn:hover { background: #334155; color: #f8fafc; }

.compare-h2h-section {
    padding: 0 40px; margin-bottom: 16px;
}
.h2h-bar {
    display: flex; align-items: center; justify-content: center; gap: 24px;
    background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px; padding: 14px 24px;
}
.h2h-record {
    display: flex; align-items: center; gap: 16px;
}
.h2h-number {
    font-size: 32px; font-weight: 900; font-family: 'Inter', sans-serif; line-height: 1;
}
.h2h-vs {
    font-size: 14px; color: #64748b; font-weight: 600;
}
.h2h-recent {
    font-size: 12px; color: #94a3b8; text-align: center; margin-top: 8px;
}

.compare-bars-section { padding: 0 40px 40px 40px; }

@media (max-width: 900px) {
    .compare-top-row {
        grid-template-columns: 1fr;
        gap: 16px;
        padding: 16px;
    }
    .compare-bars-section { padding: 0 16px 24px 16px; }
    .compare-h2h-section { padding: 0 16px; }
    .swap-btn { margin: 10px auto; }
}
`;

export async function mountCompareFeature() {
    // Inject Styles once
    if (!document.getElementById('compare-styles')) {
        const style = document.createElement('style');
        style.id = 'compare-styles';
        style.innerHTML = STYLES;
        document.head.appendChild(style);
    }

    // Nav Button
    const searchContainer = document.querySelector('.search-container');
    if (searchContainer && !document.getElementById('compare-nav-btn')) {
        const btn = document.createElement('button');
        btn.id = 'compare-nav-btn';
        btn.innerText = 'Compare Players';
        btn.className = 'filter-apply-btn';
        btn.style.marginLeft = '12px';
        btn.style.whiteSpace = 'nowrap';
        btn.addEventListener('click', openModal);

        // Ensure parent is a flex row so they sit next to each other
        const parent = searchContainer.parentNode;
        parent.style.display = 'flex';
        parent.style.alignItems = 'center';
        parent.style.justifyContent = 'center';

        // Insert it right after the search container
        parent.insertBefore(btn, searchContainer.nextSibling);
    }

    // Modal DOM
    if (!modalRoot) {
        modalRoot = document.createElement('div');
        modalRoot.id = 'compare-modal';
        modalRoot.className = 'compare-modal-overlay';

        modalRoot.innerHTML = `
            <div class="compare-modal-content">
                <div class="compare-modal-header">
                    <h2 class="compare-modal-title">Player Compare <span class="compare-modal-subtitle" id="compare-surface-label">All surfaces</span></h2>
                    <button class="compare-modal-close" id="close-compare-modal">&times;</button>
                </div>

                <div class="compare-top-row">
                    <!-- Left Column -->
                    <div style="position:relative;">
                        <input type="text" class="search-compare-input" id="compare-search-a" placeholder="Search Player A..." autocomplete="off"/>
                        <div class="typeahead-results left" id="compare-results-a"></div>
                        <div id="compare-card-a"></div>
                    </div>

                    <!-- Center Column -->
                    <div>
                        <div id="compare-surface-toggle-mount" style="display:flex; justify-content:center; margin-bottom: 12px;"></div>
                        <button class="swap-btn" id="compare-swap-btn">⇄ Swap</button>
                        <div class="compare-radar-container">
                            <canvas id="compare-radar-canvas"></canvas>
                        </div>
                    </div>

                    <!-- Right Column -->
                    <div style="position:relative;">
                        <input type="text" class="search-compare-input" id="compare-search-b" placeholder="Search Player B..." autocomplete="off"/>
                        <div class="typeahead-results right" id="compare-results-b"></div>
                        <div id="compare-card-b"></div>
                    </div>
                </div>

                <div class="compare-h2h-section" id="compare-h2h-section" style="display:none;"></div>

                <div class="compare-bars-section">
                    <div id="compare-bars-container"></div>
                </div>
            </div>
        `;
        document.body.appendChild(modalRoot);

        // Events
        document.getElementById('close-compare-modal').addEventListener('click', closeModal);
        modalRoot.addEventListener('click', (e) => {
            if (e.target === modalRoot) closeModal();
        });
        document.getElementById('compare-swap-btn').addEventListener('click', handleSwap);

        // Setup Typeaheads
        setupTypeahead('compare-search-a', 'compare-results-a', 'left');
        setupTypeahead('compare-search-b', 'compare-results-b', 'right');

        // Mount Surface Toggle
        compareSurfaceToggle = createSurfaceToggle('compare-surface-toggle-mount', async (newSurface) => {
            setCompareSurface(newSurface);
            await initCompareData(newSurface);
            // Update subtitle
            const label = document.getElementById('compare-surface-label');
            if (label) {
                label.textContent = newSurface === 'all' ? 'All surfaces' : `${newSurface} courts`;
            }
            updateUI();
        });
    }

    // Pre-fetch Data offline safely
    await initCompareData('all');
}

export function openModal() {
    if (!modalRoot) return;

    // Auto-populate Player A from the currently loaded player dashboard
    const searchInput = document.getElementById('player-search');
    if (searchInput && searchInput.value.trim()) {
        const pName = searchInput.value.trim().replace(/ /g, '_');
        playerAId = pName;
    }

    modalRoot.style.display = 'flex';
    document.body.style.overflow = 'hidden'; // Prevent background scrolling
    updateUI();
}

function closeModal() {
    if (!modalRoot) return;
    modalRoot.style.display = 'none';
    document.body.style.overflow = '';
}

function handleSwap() {
    const temp = playerAId;
    playerAId = playerBId;
    playerBId = temp;
    updateUI();
}

function setPlayerSelection(side, id) {
    if (side === 'left') playerAId = id;
    else playerBId = id;

    const input = document.getElementById(side === 'left' ? 'compare-search-a' : 'compare-search-b');
    const results = document.getElementById(side === 'left' ? 'compare-results-a' : 'compare-results-b');

    input.value = '';
    results.style.display = 'none';

    updateUI();
}

function clearPlayer(side) {
    if (side === 'left') playerAId = null;
    else playerBId = null;
    updateUI();
}

async function updateUI() {
    const pA = playerAId ? buildCompareProfile(playerAId) : null;
    const pB = playerBId ? buildCompareProfile(playerBId) : null;

    renderCompareCard('compare-card-a', pA, 'left', clearPlayer);
    renderCompareCard('compare-card-b', pB, 'right', clearPlayer);

    renderCompareRadar('compare-radar-canvas', pA, pB);
    renderCompareBars('compare-bars-container', pA, pB);

    // H2H section
    await renderH2H(playerAId, playerBId);
}

async function renderH2H(aId, bId) {
    const section = document.getElementById('compare-h2h-section');
    if (!section) return;

    if (!aId || !bId) {
        section.style.display = 'none';
        return;
    }

    const h2h = await fetchH2H(aId, bId);
    if (!h2h || h2h.totalMatches === 0) {
        section.style.display = 'block';
        section.innerHTML = `
            <div class="h2h-bar" style="justify-content: center;">
                <span style="color: #64748b; font-size: 13px;">No head-to-head matches found in dataset</span>
            </div>
        `;
        return;
    }

    // Figure out which side is A and B in the H2H data
    const isAFirst = h2h.playerA === aId;
    const winsLeft = isAFirst ? h2h.winsA : h2h.winsB;
    const winsRight = isAFirst ? h2h.winsB : h2h.winsA;

    // Most recent match
    const recent = h2h.mostRecent;
    let recentHtml = '';
    if (recent) {
        const date = recent.date ? new Date(recent.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '';
        recentHtml = `
            <div class="h2h-recent">
                Last: ${recent.tournament || ''} ${recent.round ? '(' + recent.round + ')' : ''} · ${recent.surface || ''} · ${date}
            </div>
        `;
    }

    section.style.display = 'block';
    section.innerHTML = `
        <div class="h2h-bar">
            <div class="h2h-record">
                <span class="h2h-number" style="color: #38bdf8;">${winsLeft}</span>
                <span class="h2h-vs">H2H</span>
                <span class="h2h-number" style="color: #f43f5e;">${winsRight}</span>
            </div>
        </div>
        ${recentHtml}
    `;
}

function setupTypeahead(inputId, resultsId, side) {
    const input = document.getElementById(inputId);
    const results = document.getElementById(resultsId);

    input.addEventListener('input', () => {
        const val = input.value.trim().toLowerCase();
        results.innerHTML = '';
        if (val.length < 2) {
            results.style.display = 'none';
            return;
        }

        const list = getPlayerList();
        const matches = list.filter(p => p.name.toLowerCase().includes(val)).slice(0, 5);

        if (matches.length > 0) {
            matches.forEach(m => {
                const div = document.createElement('div');
                div.className = 'typeahead-item';
                div.innerText = m.name;
                div.addEventListener('click', () => setPlayerSelection(side, m.id));
                results.appendChild(div);
            });
            results.style.display = 'block';
        } else {
            results.style.display = 'none';
        }
    });

    // Close on click outside
    document.addEventListener('click', (e) => {
        if (e.target !== input && e.target !== results) {
            results.style.display = 'none';
        }
    });
}
