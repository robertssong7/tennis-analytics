/**
 * Matchup Explorer — UI Component (Tennis IQ)
 * 
 * Fetches precomputed matchup predictions and renders them
 * as a widget showing difficult/favorable opponents with
 * pWin, confidence, and explanations.
 * 
 * Isolated module — safe to remove completely.
 */

let matchupCache = {};

async function fetchMatchups(playerId) {
    if (matchupCache[playerId]) return matchupCache[playerId];
    try {
        const resp = await fetch(`data/matchups/${playerId}.json`);
        if (!resp.ok) return null;
        const data = await resp.json();
        matchupCache[playerId] = data;
        return data;
    } catch {
        return null;
    }
}

// ═══════════════════════════════════════════════════════════════
// Render Matchup Explorer into a container
// ═══════════════════════════════════════════════════════════════
export async function renderMatchupExplorer(containerId, playerId, surface = 'All', isHome = false) {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (!playerId) {
        container.innerHTML = `
            <div style="text-align:center; color:#475569; font-size:13px; padding:24px 0;">
                Select a player to view matchup predictions
            </div>
        `;
        return;
    }

    container.innerHTML = `
        <div style="text-align:center; color:#64748b; font-size:12px; padding:16px;">
            Loading matchups…
        </div>
    `;

    const data = await fetchMatchups(playerId);
    if (!data || !data[surface]) {
        container.innerHTML = `
            <div style="text-align:center; color:#475569; font-size:13px; padding:16px;">
                No matchup data available for this player/surface.
            </div>
        `;
        return;
    }

    const surfData = data[surface];
    let currentTab = 'difficult';

    function render() {
        if (isHome) {
            container.innerHTML = `
                <div class="matchup-legend" style="display:flex; justify-content:center; gap:16px; font-size:11px; color:#64748b; margin-bottom:12px; background:rgba(255,255,255,0.6); padding:8px; border-radius:8px;">
                    <span><strong style="color:#0f172a;">%</strong> = Predicted Win Probability</span>
                    <span><strong style="color:#0f172a;">Confidence (LOW/HIGH)</strong> = Based on sample size</span>
                </div>
                <div class="matchup-explorer-home" style="display:grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px;">
                    <div class="matchup-explorer" style="background:#0f172a; box-shadow:0 10px 30px rgba(0,0,0,0.15);">
                        <div style="padding:12px; border-bottom:1px solid rgba(255,255,255,0.06); font-size:13px; font-weight:800; color:#ef4444; text-transform:uppercase; letter-spacing:0.5px; text-align:center;">
                            Toughest Matchups
                        </div>
                        <div class="matchup-list-container" style="max-height:none;">
                            ${surfData && surfData.difficult ? surfData.difficult.slice(0, 3).map((m, i) => renderMatchupRow(m, i, 'difficult', true)).join('') : '<div style="padding:12px; color:#64748b; text-align:center;">No data</div>'}
                        </div>
                    </div>
                    <div class="matchup-explorer" style="background:#0f172a; box-shadow:0 10px 30px rgba(0,0,0,0.15);">
                        <div style="padding:12px; border-bottom:1px solid rgba(255,255,255,0.06); font-size:13px; font-weight:800; color:#22c55e; text-transform:uppercase; letter-spacing:0.5px; text-align:center;">
                            Most Favorable
                        </div>
                        <div class="matchup-list-container" style="max-height:none;">
                            ${surfData && surfData.favorable ? surfData.favorable.slice(0, 3).map((m, i) => renderMatchupRow(m, i, 'favorable', true)).join('') : '<div style="padding:12px; color:#64748b; text-align:center;">No data</div>'}
                        </div>
                    </div>
                </div>
            `;
        } else {
            const list = currentTab === 'difficult' ? surfData?.difficult : surfData?.favorable;
            container.innerHTML = `
                <div class="matchup-explorer">
                    <div class="matchup-tabs">
                        <button class="matchup-tab ${currentTab === 'difficult' ? 'active' : ''}" data-tab="difficult" style="${currentTab === 'difficult' ? 'color:#ef4444; border-bottom-color:#ef4444;' : ''}">
                            Toughest Matchups
                        </button>
                        <button class="matchup-tab ${currentTab === 'favorable' ? 'active' : ''}" data-tab="favorable" style="${currentTab === 'favorable' ? 'color:#22c55e; border-bottom-color:#22c55e;' : ''}">
                            Most Favorable
                        </button>
                    </div>
                    <div class="matchup-list-container">
                        ${(list || []).map((m, i) => renderMatchupRow(m, i, currentTab, false)).join('')}
                    </div>
                </div>
            `;
        }

        // Tab click handlers
        container.querySelectorAll('.matchup-tab').forEach(btn => {
            btn.addEventListener('click', () => {
                currentTab = btn.dataset.tab;
                render();
            });
        });

        // Expand/collapse reason rows
        container.querySelectorAll('.matchup-row-main').forEach(row => {
            row.addEventListener('click', () => {
                const reasons = row.nextElementSibling;
                if (reasons && reasons.classList.contains('matchup-reasons')) {
                    reasons.classList.toggle('expanded');
                    const chevron = row.querySelector('.matchup-chevron');
                    if (chevron) chevron.classList.toggle('expanded');
                }
            });
        });
    }

    render();
}

function renderMatchupRow(matchup, index, tab, isHome = false) {
    const { opponentName, pWin, confidence, reasons } = matchup;
    const pWinDisplay = Math.round(pWin * 100);
    const barWidth = pWinDisplay;

    // Color: for difficult tab, lower pWin = more red. For favorable, higher = more green.
    const barColor = tab === 'difficult'
        ? `hsl(${pWinDisplay * 1.2}, 70%, 50%)`
        : `hsl(${pWinDisplay * 1.2}, 70%, 45%)`;

    const confidenceBadge = {
        high: { bg: 'rgba(34,197,94,0.15)', color: '#22c55e', label: 'HIGH' },
        medium: { bg: 'rgba(234,179,8,0.15)', color: '#eab308', label: 'MED' },
        low: { bg: 'rgba(148,163,184,0.15)', color: '#94a3b8', label: 'LOW' },
    }[confidence] || { bg: 'rgba(148,163,184,0.15)', color: '#94a3b8', label: '—' };

    return `
        <div class="matchup-item">
            <div class="matchup-row-main" style="cursor:pointer;">
                <span class="matchup-rank" style="color:${tab === 'difficult' ? '#f43f5e' : '#22c55e'}; font-weight:900; font-size:13px; min-width:24px; text-align:center;">
                    ${index + 1}
                </span>
                <span class="matchup-opponent-name">${opponentName}</span>
                <div class="matchup-pwin-bar">
                    <div class="matchup-pwin-fill" style="width:${barWidth}%; background:${barColor};"></div>
                </div>
                <span class="matchup-pwin-val">${pWinDisplay}%</span>
                <span class="matchup-confidence" style="background:${confidenceBadge.bg}; color:${confidenceBadge.color};">
                    ${confidenceBadge.label}
                </span>
                <span class="matchup-chevron ${isHome ? 'expanded' : ''}">▾</span>
            </div>
            <div class="matchup-reasons ${isHome ? 'expanded' : ''}">
                ${(reasons || []).map(r => `<div class="matchup-reason">→ ${r}</div>`).join('')}
            </div>
        </div>
    `;
}

// ═══════════════════════════════════════════════════════════════
// CSS for Matchup Explorer (injected once)
// ═══════════════════════════════════════════════════════════════
export function injectMatchupStyles() {
    if (document.getElementById('matchup-explorer-styles')) return;

    const style = document.createElement('style');
    style.id = 'matchup-explorer-styles';
    style.innerHTML = `
    .matchup-explorer {
        background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255,255,255,0.06);
        border-radius: 12px; overflow: hidden;
    }
    .matchup-tabs {
        display: flex; border-bottom: 1px solid rgba(255,255,255,0.06);
    }
    .matchup-tab {
        flex: 1; padding: 12px; background: transparent; border: none;
        color: #64748b; font-size: 12px; font-weight: 700; cursor: pointer;
        text-transform: uppercase; letter-spacing: 0.5px; transition: all 0.2s;
    }
    .matchup-tab:hover { color: #cbd5e1; background: rgba(255,255,255,0.02); }
    .matchup-tab.active {
        color: #f8fafc; background: rgba(255,255,255,0.04);
        border-bottom: 2px solid #38bdf8;
    }
    .matchup-list-container { max-height: 480px; overflow-y: auto; }
    .matchup-item { border-bottom: 1px solid rgba(255,255,255,0.03); }
    .matchup-row-main {
        display: flex; align-items: center; gap: 10px; padding: 10px 14px;
        transition: background 0.15s;
    }
    .matchup-row-main:hover { background: rgba(255,255,255,0.03); }
    .matchup-opponent-name {
        flex: 1; font-size: 13px; color: #e2e8f0; font-weight: 500;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .matchup-pwin-bar {
        width: 80px; height: 6px; background: rgba(255,255,255,0.06);
        border-radius: 3px; overflow: hidden; flex-shrink: 0;
    }
    .matchup-pwin-fill { height: 100%; border-radius: 3px; transition: width 0.3s; }
    .matchup-pwin-val {
        font-size: 13px; font-weight: 800; color: #f8fafc; min-width: 36px; text-align: right;
        font-family: 'Inter', 'JetBrains Mono', monospace;
    }
    .matchup-confidence {
        font-size: 9px; font-weight: 800; padding: 2px 6px; border-radius: 4px;
        text-transform: uppercase; letter-spacing: 0.5px; min-width: 32px; text-align: center;
    }
    .matchup-chevron {
        color: #475569; font-size: 11px; transition: transform 0.2s; min-width: 14px; text-align: center;
    }
    .matchup-chevron.expanded { transform: rotate(180deg); }
    .matchup-reasons {
        display: none; padding: 0 14px 10px 48px;
    }
    .matchup-reasons.expanded { display: block; }
    .matchup-reason {
        font-size: 12px; color: #94a3b8; padding: 3px 0; line-height: 1.4;
    }
    `;
    document.head.appendChild(style);
}
