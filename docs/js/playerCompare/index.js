import { initCompareData, getPlayerList, buildCompareProfile } from './playerCompareModel.js';
import { renderCompareCard } from './PlayerCompareCard.js';
import { renderCompareRadar } from './CompareRadarPentagon.js';
import { renderCompareBars } from './PlayerCompareBars.js';

let modalRoot = null;
let playerAId = null;
let playerBId = null;

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

.compare-bars-section { padding: 0 40px 40px 40px; }

@media (max-width: 900px) {
    .compare-top-row {
        grid-template-columns: 1fr;
        gap: 16px;
        padding: 16px;
    }
    .compare-bars-section { padding: 0 16px 24px 16px; }
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
        btn.innerHTML = '⚖️ Compare';
        btn.className = 'filter-apply-btn'; // Steal existing blue button styling
        btn.style.marginRight = '12px'; // Add slight margin before search bar
        btn.addEventListener('click', openModal);
        // Insert it right before the search container
        searchContainer.parentNode.insertBefore(btn, searchContainer);
    }

    // Modal DOM
    if (!modalRoot) {
        modalRoot = document.createElement('div');
        modalRoot.id = 'compare-modal';
        modalRoot.className = 'compare-modal-overlay';

        modalRoot.innerHTML = `
            <div class="compare-modal-content">
                <div class="compare-modal-header">
                    <h2 class="compare-modal-title">Player Compare <span class="compare-modal-subtitle">Scope: All surfaces (tour percentiles)</span></h2>
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
    }

    // Pre-fetch Data offline safely
    await initCompareData();
}

export function openModal() {
    if (!modalRoot) return;

    // Auto-populate Player A if a player is currently active on the dashboard
    const searchInput = document.getElementById('player-search');
    if (searchInput && searchInput.value && !playerAId) {
        const pName = searchInput.value.trim().replace(/ /g, '_');
        setPlayerSelection('left', pName);
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

function updateUI() {
    const pA = playerAId ? buildCompareProfile(playerAId) : null;
    const pB = playerBId ? buildCompareProfile(playerBId) : null;

    renderCompareCard('compare-card-a', pA, 'left', clearPlayer);
    renderCompareCard('compare-card-b', pB, 'right', clearPlayer);

    renderCompareRadar('compare-radar-canvas', pA, pB);
    renderCompareBars('compare-bars-container', pA, pB);
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
