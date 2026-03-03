/* ═══════════════════════════════════════════════════════════════════════
   TennisIQ — Frontend Application (Static + Dynamic Mode)
   ═══════════════════════════════════════════════════════════════════════ */

// ─── Mode Detection ──────────────────────────────────────────────────
let STATIC_MODE = false;
let allPlayers = [];

// ─── State ───────────────────────────────────────────────────────────
const FEATURE_FLAGS = {
    showScoutReport: false,
    ENABLE_RALLY_COMFORT: true,
    ENABLE_PRESSURE_STATE: true,
    ENABLE_OPPONENT_SHIFT: true,
};
let expandedTables = {};
let currentPlayer = null;
let currentSurface = 'all'; // Surface toggle state

// ─── DOM Refs ────────────────────────────────────────────────────────
const searchInput = document.getElementById('player-search');
const searchDropdown = document.getElementById('search-dropdown');
const dashboard = document.getElementById('dashboard');
const homeScreen = document.getElementById('home-screen');
const loadingOverlay = document.getElementById('loading-overlay');

// ─── Helpers ─────────────────────────────────────────────────────────
function formatShotType(s) {
    return s ? s.replace(/_/g, ' ') : '—';
}
function toTitleCase(s) {
    return s.replace(/_/g, ' ').replace(/\w\S*/g, w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase());
}

function formatPct(v) { return (v * 100).toFixed(1) + '%'; }

function formatAdjEff(val) {
    if (val === undefined || isNaN(val)) return '—';
    const sign = val >= 0 ? '+' : '';
    const cls = val >= 0 ? '<span style="color:var(--green)">' : '<span style="color:var(--red)">';
    return `${cls}${sign}${(val * 100).toFixed(1)}%</span>`;
}

function rowClass(conf) {
    if (conf === 'insufficient') return 'row-hidden';
    return '';
}

function showLoading() { loadingOverlay.classList.remove('hidden'); }
function hideLoading() { loadingOverlay.classList.add('hidden'); }

// ─── Accordions & UI Setup ───────────────────────────────────────────
function setupAccordions() {
    document.querySelectorAll('.accordion-btn').forEach(btn => {
        if (btn.dataset.bound) return;
        btn.dataset.bound = 'true';
        btn.addEventListener('click', () => {
            const isExpanded = btn.getAttribute('aria-expanded') === 'true';
            btn.setAttribute('aria-expanded', !isExpanded);
            const content = document.getElementById(btn.getAttribute('aria-controls'));
            if (content) {
                if (isExpanded) {
                    content.classList.add('collapsed');
                } else {
                    content.classList.remove('collapsed');
                }
            }
        });
    });
}

function renderExpandableTable(tableKey, tbodyId, rows, renderRowFn) {
    const tbody = document.getElementById(tbodyId);
    if (!tbody) return;
    const isExpanded = !!expandedTables[tableKey];
    const visibleRows = (rows.length > 8 && !isExpanded) ? rows.slice(0, 8) : rows;
    tbody.innerHTML = visibleRows.map(renderRowFn).join('');

    const btn = document.querySelector(`.show-more-btn[data-target="${tbodyId}"]`);
    if (!btn) return;
    if (rows.length <= 8) {
        btn.style.display = 'none';
        return;
    }
    btn.style.display = 'block';
    btn.textContent = isExpanded ? 'Show less' : `Show more (${rows.length - 8})`;
    btn.onclick = () => {
        expandedTables[tableKey] = !isExpanded;
        renderExpandableTable(tableKey, tbodyId, rows, renderRowFn);
    };
}

function splitAndRender(data, moduleId, containerIdBase, rowHtmlFn) {
    // Filter undefined values
    const validData = data.filter(d => d.adjustedEffectiveness !== undefined);
    const strengths = validData.filter(d => d.adjustedEffectiveness > 0)
        .sort((a, b) => (b.adjustedEffectiveness - a.adjustedEffectiveness) || (b.total - a.total));
    const weaknesses = validData.filter(d => d.adjustedEffectiveness <= 0)
        .sort((a, b) => (a.adjustedEffectiveness - b.adjustedEffectiveness) || (b.total - a.total));

    renderExpandableTable(`${moduleId}:strengths`, `${containerIdBase}-strengths-tbody`, strengths, rowHtmlFn);
    renderExpandableTable(`${moduleId}:weaknesses`, `${containerIdBase}-weaknesses-tbody`, weaknesses, rowHtmlFn);
}

// ─── Data Fetching ───────────────────────────────────────────────────
async function fetchJSON(url) {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`Fetch error: ${resp.status}`);
    return resp.json();
}

async function fetchPlayerData(playerName, endpoint) {
    if (STATIC_MODE) {
        // Load surface-specific data if a surface is selected
        const basePath = currentSurface === 'all'
            ? `data/players/${playerName}`
            : `data/players/${playerName}/surface/${currentSurface}`;
        try {
            return await fetchJSON(`${basePath}/${endpoint}.json`);
        } catch {
            // Fall back to "all" if surface-specific data doesn't exist
            try {
                return await fetchJSON(`data/players/${playerName}/${endpoint}.json`);
            } catch {
                return endpoint === 'pattern-inference' ? { winning: [], losing: [] } : [];
            }
        }
    }
    const params = endpoint === 'coverage' || endpoint === 'insights' ? {} : getFilters();
    // Override surface with toggle value
    if (currentSurface !== 'all') params.surface = currentSurface;
    const q = new URLSearchParams(params).toString();
    const sep = endpoint === 'serve-plus-one' ? 'serve-plus-one' : endpoint;
    return fetchJSON(`/api/player/${encodeURIComponent(playerName)}/${sep}${q ? '?' + q : ''}`);
}

function getFilters() {
    const f = {};
    const surface = document.getElementById('filter-surface').value;
    const dateFrom = document.getElementById('filter-date-from').value;
    const dateTo = document.getElementById('filter-date-to').value;
    const serveNum = document.getElementById('filter-serve-num').value;
    const side = document.getElementById('filter-side').value;
    if (surface) f.surface = surface;
    if (dateFrom) f.dateFrom = dateFrom;
    if (dateTo) f.dateTo = dateTo;
    if (serveNum) f.serveNumber = serveNum;
    if (side) f.side = side;
    f.minN = 15; // Set minimum sample size silently 
    return f;
}

// ═══════════════════════════════════════════════════════════════════════
// INITIALIZATION
// ═══════════════════════════════════════════════════════════════════════
async function init() {
    try {
        // Mount FIFA-Style Compare Feature
        import('./js/playerCompare/index.js?v=srf2').then(mod => {
            mod.mountCompareFeature();
        }).catch(err => console.error("Compare Map Init Failed:", err));

        // Mount Home Screen
        import('./js/home.js').then(mod => {
            mod.initHomeScreen();
        }).catch(err => console.error("Home Screen Init Failed:", err));

        // Try API first to see if backend is running
        await fetchJSON('/api/players?q=a');
        STATIC_MODE = false;
        console.log('TennisIQ: Dynamic API mode');
    } catch {
        // Fallback to static mode
        try {
            allPlayers = await fetchJSON('data/players.json');
            STATIC_MODE = true;
            console.log(`TennisIQ: Static mode — ${allPlayers.length} players loaded`);
            // In static mode, hide the full filter bar but keep surface toggle in player header
            document.getElementById('filter-bar').style.display = 'none';
        } catch (e) {
            console.error('TennisIQ: Failed to load in both Dynamic and Static modes.');
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════
// SEARCH
// ═══════════════════════════════════════════════════════════════════════
let searchTimeout = null;

searchInput.addEventListener('input', () => {
    clearTimeout(searchTimeout);
    const q = searchInput.value.trim();
    if (q.length < 2) { searchDropdown.classList.remove('active'); return; }

    searchTimeout = setTimeout(async () => {
        try {
            let players;
            if (STATIC_MODE) {
                const lower = q.toLowerCase();
                players = allPlayers.filter(p =>
                    p.toLowerCase().includes(lower) || p.replace(/_/g, ' ').toLowerCase().includes(lower)
                );
            } else {
                players = await fetchJSON(`/api/players?q=${encodeURIComponent(q)}`);
            }
            renderDropdown(players, q);
        } catch (err) {
            console.error(err);
        }
    }, 150);
});

searchInput.addEventListener('focus', () => {
    if (searchDropdown.children.length > 0) searchDropdown.classList.add('active');
});

document.addEventListener('click', (e) => {
    if (!document.getElementById('search-container').contains(e.target)) {
        searchDropdown.classList.remove('active');
    }
});

function renderDropdown(players, q) {
    if (players.length === 0) {
        searchDropdown.innerHTML = '<div class="search-item" style="color:var(--text-muted)">No players found</div>';
        searchDropdown.classList.add('active');
        return;
    }
    const regex = new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
    searchDropdown.innerHTML = players.map(p => {
        const displayName = p.replace(/_/g, ' ');
        return `<div class="search-item" data-player="${p}">${displayName.replace(regex, '<mark>$1</mark>')}</div>`;
    }).join('');
    searchDropdown.classList.add('active');

    searchDropdown.querySelectorAll('.search-item').forEach(item => {
        item.addEventListener('click', () => {
            const name = item.dataset.player;
            searchInput.value = name.replace(/_/g, ' ');
            searchDropdown.classList.remove('active');
            loadPlayer(name);
        });
    });
}

// ═══════════════════════════════════════════════════════════════════════
// SURFACE TOGGLE (Player Dashboard)
// ═══════════════════════════════════════════════════════════════════════
let playerSurfaceToggle = null;

async function mountPlayerSurfaceToggle() {
    if (playerSurfaceToggle) return; // Already mounted
    try {
        const { createSurfaceToggle } = await import('./js/SurfaceToggle.js');
        playerSurfaceToggle = createSurfaceToggle('player-surface-toggle', (newSurface) => {
            currentSurface = newSurface;
            if (currentPlayer) loadPlayer(currentPlayer);
        });
    } catch (err) {
        console.error('Failed to mount surface toggle:', err);
    }
}

// ═══════════════════════════════════════════════════════════════════════
// LOAD PLAYER DATA
// ═══════════════════════════════════════════════════════════════════════
async function loadPlayer(name) {
    expandedTables = {};
    currentPlayer = name;
    showLoading();
    homeScreen.style.display = 'none';

    // Mount surface toggle if not already
    await mountPlayerSurfaceToggle();

    try {
        const [coverage, patterns, serve, serveOne, compare, directions, insights, inference, serveDetailed] =
            await Promise.all([
                fetchPlayerData(name, 'coverage'),
                fetchPlayerData(name, 'patterns'),
                fetchPlayerData(name, 'serve'),
                fetchPlayerData(name, 'serve-plus-one'),
                fetchPlayerData(name, 'compare'),
                fetchPlayerData(name, 'direction-patterns'),
                FEATURE_FLAGS.showScoutReport ? fetchPlayerData(name, 'insights') : Promise.resolve([]),
                fetchPlayerData(name, 'pattern-inference'),
                fetchPlayerData(name, 'serve-detailed')
            ]);

        // Fetch new feature data (non-blocking — don't break player load if these fail)
        const [rallyComfort, pressureState, opponentShifts] = await Promise.all([
            FEATURE_FLAGS.ENABLE_RALLY_COMFORT ? fetchPlayerData(name, 'rally-comfort').catch(() => null) : null,
            FEATURE_FLAGS.ENABLE_PRESSURE_STATE ? fetchPlayerData(name, 'pressure-state').catch(() => null) : null,
            FEATURE_FLAGS.ENABLE_OPPONENT_SHIFT ? fetchPlayerData(name, 'opponent-shifts').catch(() => null) : null,
        ]);

        renderPlayerHeader(name, coverage);

        // Mount the Phase 7 Tour-Percentile Radar dynamically first to build the HTML shell
        try {
            const { renderRadar } = await import('./js/playerRadar/RadarComponent.js?v=srf2');
            await renderRadar('radar-module-container', { patterns, directionPatterns: directions, servePlusOne: serveOne });
        } catch (radarErr) {
            console.error('Failed to mount Player Radar:', radarErr);
        }

        const insightsSection = document.getElementById('insights-section');
        if (FEATURE_FLAGS.showScoutReport) {
            if (insightsSection) insightsSection.style.display = 'block';
            renderInsights(insights);
        } else {
            if (insightsSection) insightsSection.style.display = 'none';
        }

        renderCoverage(coverage);
        renderPatterns(patterns);
        renderServeDetailed(serveDetailed);
        renderServe(serve);
        renderServeOne(serveOne);
        renderDirections(directions);
        renderCompare(compare);
        renderInference(inference);

        // New features
        renderRallyComfort(rallyComfort);
        renderPressureState(pressureState);
        renderOpponentShifts(opponentShifts);

        dashboard.classList.remove('hidden');
        setupAccordions();
    } catch (err) {
        console.error('Failed to load player:', err);
        alert('Failed to load player data. Check the console for details.');
    } finally {
        hideLoading();
    }
}

// ═══════════════════════════════════════════════════════════════════════
// RENDER: Player Header
// ═══════════════════════════════════════════════════════════════════════
function renderPlayerHeader(name, coverage) {
    document.getElementById('player-name-display').textContent = name.replace(/_/g, ' ');
    const t = coverage.totals;
    document.getElementById('player-meta').textContent = '';

    // ── Compact stat boxes next to player name ──
    let statsRow = document.getElementById('player-stat-boxes');
    if (!statsRow) {
        statsRow = document.createElement('div');
        statsRow.id = 'player-stat-boxes';
        statsRow.style.cssText = 'display:flex; gap:12px; margin-top:10px; flex-wrap:wrap;';
        const nameEl = document.getElementById('player-name-display');
        if (nameEl && nameEl.parentNode) nameEl.parentNode.appendChild(statsRow);
    }
    const boxStyle = 'background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08); border-radius:8px; padding:8px 14px; text-align:center; min-width:80px;';
    const labelStyle = 'font-size:10px; color:#94a3b8; text-transform:uppercase; font-weight:600; letter-spacing:0.5px;';
    const valueStyle = 'font-size:18px; font-weight:700; color:#f8fafc; margin-top:2px;';
    statsRow.innerHTML = `
        <div style="${boxStyle}"><div style="${labelStyle}">Matches</div><div style="${valueStyle}">${t.total_matches}</div></div>
        <div style="${boxStyle}"><div style="${labelStyle}">Points</div><div style="${valueStyle}">${Number(t.total_points).toLocaleString()}</div></div>
    `;
}

// ═══════════════════════════════════════════════════════════════════════
// RENDER: Insights (Top 3 Scout Notes)
// ═══════════════════════════════════════════════════════════════════════
function renderInsights(insights) {
    const grid = document.getElementById('insights-grid');
    if (!insights || insights.length === 0) {
        grid.innerHTML = '<p style="color:var(--text-muted);padding:12px">No specific scout notes available for this context.</p>';
        return;
    }
    const top3 = insights.slice(0, 3);
    grid.innerHTML = top3.map(i => `
    <div class="insight-item ${i.type}">
      <div class="insight-title">${i.icon} ${i.title}</div>
      <div class="insight-detail">${i.detail}</div>
    </div>
  `).join('');
}

// ═══════════════════════════════════════════════════════════════════════
// RENDER: Coverage
// ═══════════════════════════════════════════════════════════════════════
function renderCoverage(coverage) {
    const t = coverage.totals;

    // Inject totals directly into header so they are visible even when collapsed
    const headerText = document.querySelector('#coverage-section .header-text');
    if (headerText) {
        let inlineTotals = headerText.querySelector('.coverage-totals-inline');
        if (!inlineTotals) {
            inlineTotals = document.createElement('div');
            inlineTotals.className = 'coverage-totals-inline';
            inlineTotals.style.display = 'flex';
            inlineTotals.style.gap = '16px';
            inlineTotals.style.marginTop = '8px';
            inlineTotals.style.fontSize = '13px';
            headerText.appendChild(inlineTotals);

            const subtitle = headerText.querySelector('.card-subtitle');
            if (subtitle) subtitle.style.display = 'none';
        }
        inlineTotals.innerHTML = `
            <div><span style="color:#f8fafc; font-weight:700;">${t.total_matches}</span> <span style="color:#94a3b8">Matches</span></div>
            <div><span style="color:#f8fafc; font-weight:700;">${Number(t.total_points).toLocaleString()}</span> <span style="color:#94a3b8">Points</span></div>
        `;
    }

    const oldTotals = document.getElementById('coverage-totals');
    if (oldTotals) oldTotals.style.display = 'none';

    const rows = coverage.breakdown;
    const years = [...new Set(rows.map(r => r.year))].sort((a, b) => b - a);
    const surfaces = [...new Set(rows.map(r => r.surface))].sort();

    const thead = document.getElementById('coverage-thead');
    thead.innerHTML = '<th>Year</th>' + surfaces.map(s => `<th>${s}</th>`).join('') + '<th>Total</th>';

    const tbody = document.getElementById('coverage-tbody');
    tbody.innerHTML = years.map(year => {
        const yearRows = rows.filter(r => r.year === year);
        let totalMatches = 0;
        const cells = surfaces.map(surface => {
            const cell = yearRows.find(r => r.surface === surface);
            if (cell) {
                totalMatches += parseInt(cell.matches);
                return `<td class="num">${cell.matches} <span style="color:var(--text-muted);font-size:0.7rem">(${cell.points}pts)</span></td>`;
            }
            return '<td style="color:var(--text-muted)">—</td>';
        });
        return `<tr><td class="num" style="font-weight:700">${year}</td>${cells.join('')}<td class="num" style="font-weight:600;color:var(--accent)">${totalMatches}</td></tr>`;
    }).join('');
}

// ═══════════════════════════════════════════════════════════════════════
// RENDER: Serve Detailed (1st vs 2nd Serve Summary)
// ═══════════════════════════════════════════════════════════════════════
function renderServeDetailed(data) {
    const panel = document.getElementById('serve-detailed-panel');
    if (!panel) return;

    // Hide section entirely if data is bogus (100% 1st serve in means no 1st/2nd distinction)
    const section = document.getElementById('serve-detailed-section');
    if (!data || !data.totalServePoints || data.firstServeInPct >= 0.99) {
        if (section) section.style.display = 'none';
        return;
    }
    if (section) section.style.display = 'block';

    const metrics = [
        { label: '1st Serve In', value: formatPct(data.firstServeInPct), sub: `${data.firstServeIn} / ${data.totalServePoints}`, color: '#38bdf8' },
        { label: '1st Serve Win', value: formatPct(data.firstServeWinPct), sub: `${data.firstServeWon} / ${data.firstServeIn}`, color: '#22c55e' },
        { label: '2nd Serve Win', value: formatPct(data.secondServeWinPct), sub: `${data.secondServeWon} / ${data.secondServeTotal}`, color: '#f59e0b' },
    ];
    if (data.aces > 0) {
        metrics.push({ label: 'Aces', value: `${data.aces}`, sub: formatPct(data.acePct) + ' of serves', color: '#a78bfa' });
    }
    if (data.doubleFaults > 0) {
        metrics.push({ label: 'Double Faults', value: `${data.doubleFaults}`, sub: formatPct(data.doubleFaultPct) + ' of serves', color: '#f43f5e' });
    }

    panel.innerHTML = metrics.map(m => `
        <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; padding: 14px 16px; text-align: center;">
            <div style="font-size: 11px; color: #94a3b8; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; margin-bottom: 6px;">${m.label}</div>
            <div style="font-size: 26px; font-weight: 800; color: ${m.color}; line-height: 1;">${m.value}</div>
            <div style="font-size: 11px; color: #64748b; margin-top: 4px;">${m.sub}</div>
        </div>
    `).join('');
}

// ═══════════════════════════════════════════════════════════════════════
// RENDER: Shot Patterns
// ═══════════════════════════════════════════════════════════════════════
function renderPatterns(patterns) {
    const renderRow = (p) => `
    <tr class="${rowClass(p.confidence)}">
      <td style="font-weight:600">${formatShotType(p.shotType)}</td>
      <td class="num">${p.total}</td>
      <td class="num">${formatPct(p.winnerRate)}</td>
      <td class="num">${formatAdjEff(p.adjustedEffectiveness)}</td>
    </tr>
  `;
    splitAndRender(patterns, 'shotPattern', 'patterns', renderRow);
}

// ═══════════════════════════════════════════════════════════════════════
// RENDER: Serve Analysis
// ═══════════════════════════════════════════════════════════════════════
function renderServe(serve) {
    const tbody = document.getElementById('serve-tbody');
    tbody.innerHTML = serve.map(s => `
    <tr class="${rowClass(s.confidence)}">
      <td style="font-weight:600">${s.direction.replace('_', ' ')}</td>
      <td class="num">${s.total}</td>
      <td class="num" style="color:var(--green)">${formatPct(s.winnerRate)}</td>
      <td class="num" style="color:var(--red)">${formatPct(s.errorRate)}</td>
    </tr>
  `).join('');
}

// ═══════════════════════════════════════════════════════════════════════
// RENDER: Serve + 1
// ═══════════════════════════════════════════════════════════════════════
function renderServeOne(serveOne) {
    const renderRow = (s) => `
    <tr class="${rowClass(s.confidence)}">
      <td style="font-weight:600">${s.serveDir.replace('_', ' ')} → ${formatShotType(s.responseType)}</td>
      <td class="num">${s.total}</td>
      <td class="num">${formatPct(s.winnerRate)}</td>
      <td class="num">${formatAdjEff(s.adjustedEffectiveness)}</td>
    </tr>
  `;
    splitAndRender(serveOne, 'servePlusOne', 'serve-one', renderRow);
}

// ═══════════════════════════════════════════════════════════════════════
// RENDER: Direction Patterns
// ═══════════════════════════════════════════════════════════════════════
function renderDirections(directions) {
    const renderRow = (d) => `
    <tr class="${rowClass(d.confidence)}">
      <td style="font-weight:600">${formatShotType(d.shotType)} <span style="font-weight:normal;color:var(--text-muted)">(${d.direction.replace('_', ' ')})</span></td>
      <td class="num">${d.total}</td>
      <td class="num">${formatPct(d.winnerRate)}</td>
      <td class="num">${formatAdjEff(d.adjustedEffectiveness)}</td>
    </tr>
  `;
    splitAndRender(directions, 'shotDirection', 'direction', renderRow);
}

// ═══════════════════════════════════════════════════════════════════════
// RENDER: Win vs Loss Comparison
// ═══════════════════════════════════════════════════════════════════════
function renderCompare(compare) {
    const renderRow = (p) => `
    <tr class="${rowClass(p.confidence)}">
      <td style="font-weight:600">${formatShotType(p.shotType)}</td>
      <td class="num">${p.total}</td>
      <td class="num">${formatPct(p.winnerRate)}</td>
      <td class="num">${formatAdjEff(p.adjustedEffectiveness)}</td>
    </tr>
  `;

    // Sort by adjustedEffectiveness (highest positive first), then total N desc
    const wins = (compare.wins || []).sort((a, b) => ((b.adjustedEffectiveness || 0) - (a.adjustedEffectiveness || 0)) || ((b.total || 0) - (a.total || 0)));
    const losses = (compare.losses || []).sort((a, b) => ((a.adjustedEffectiveness || 0) - (b.adjustedEffectiveness || 0)) || ((b.total || 0) - (a.total || 0)));

    renderExpandableTable('compare:wins', 'compare-win-tbody', wins, renderRow);
    renderExpandableTable('compare:losses', 'compare-loss-tbody', losses, renderRow);
}

// ═══════════════════════════════════════════════════════════════════════
// RENDER: Pattern Inference
// ═══════════════════════════════════════════════════════════════════════
function renderInference(inference) {
    if (!inference) return;
    const renderRow = (seq) => `
    <tr>
      <td style="font-weight:600">${seq.sequence.map(s => toTitleCase(s)).join(' → ')}</td>
      <td class="num">${seq.total}</td>
      <td class="num">${formatPct(seq.winnerRate)}</td>
      <td class="num">${formatAdjEff(seq.uplift)}</td>
    </tr>
  `;

    // Sort winning sequences from best % win to worst, then total desc
    const winning = (inference.winning || []).sort((a, b) => (b.winnerRate - a.winnerRate) || (b.total - a.total));
    // Sort losing sequences from lowest % win to higher (ascending), then total desc
    const losing = (inference.losing || []).sort((a, b) => (a.winnerRate - b.winnerRate) || (b.total - a.total));

    renderExpandableTable('patternInference:winning', 'inference-winning-tbody', winning, renderRow);
    renderExpandableTable('patternInference:losing', 'inference-losing-tbody', losing, renderRow);
}

// ═══════════════════════════════════════════════════════════════════════
// EVENTS
// ═══════════════════════════════════════════════════════════════════════

document.getElementById('filter-apply').addEventListener('click', () => {
    if (currentPlayer) loadPlayer(currentPlayer);
});

document.getElementById('filter-reset').addEventListener('click', () => {
    document.getElementById('filter-surface').value = '';
    document.getElementById('filter-date-from').value = '';
    document.getElementById('filter-date-to').value = '';
    document.getElementById('filter-serve-num').value = '';
    document.getElementById('filter-side').value = '';
    currentSurface = 'all';
    if (playerSurfaceToggle) playerSurfaceToggle.setValue('all');
    if (currentPlayer) loadPlayer(currentPlayer);
});

searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        const firstItem = searchDropdown.querySelector('.search-item');
        if (firstItem && firstItem.dataset.player) {
            searchInput.value = firstItem.dataset.player.replace(/_/g, ' ');
            searchDropdown.classList.remove('active');
            loadPlayer(firstItem.dataset.player);
        }
    }
});

// ═══════════════════════════════════════════════════════════════════════
// RALLY COMFORT ZONE
// ═══════════════════════════════════════════════════════════════════════
function renderRallyComfort(data) {
    const section = document.getElementById('rally-comfort-section');
    if (!section) return;
    if (!FEATURE_FLAGS.ENABLE_RALLY_COMFORT || !data || !data.buckets || data.buckets.length === 0) {
        section.style.display = 'none';
        return;
    }

    section.style.display = 'block';

    // Summary: Peak vs Tour avg
    const summaryEl = document.getElementById('rally-comfort-summary');
    if (summaryEl && data.peak_bucket) {
        const peakLabel = data.peak_bucket === '13+' ? '13+' : data.peak_bucket;
        summaryEl.innerHTML = `
            <span style="font-weight:600;color:var(--text-primary);">Peak: <span style="color:#2563EB;">${peakLabel} balls</span></span>
            <span style="margin-left:16px;color:#888;font-size:13px;">Tour avg loaded from benchmarks</span>
        `;
    }

    // Bar chart (CSS-only horizontal bars)
    const barsEl = document.getElementById('rally-comfort-bars');
    if (barsEl) {
        const maxRate = Math.max(...data.buckets.map(b => b.pt_win_rate), 0.01);
        barsEl.innerHTML = data.buckets.map(b => {
            const pct = (b.pt_win_rate * 100).toFixed(1);
            const barWidth = (b.pt_win_rate / Math.max(maxRate, 0.01)) * 100;
            const isPeak = b.range === data.peak_bucket;
            const barColor = isPeak ? '#2563EB' : '#D1D5DB';
            const textColor = isPeak ? '#2563EB' : 'var(--text-muted)';
            return `
                <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
                    <span style="min-width:50px;font-size:13px;font-weight:500;color:${textColor};text-align:right;">${b.range}</span>
                    <div style="flex:1;position:relative;height:24px;background:rgba(255,255,255,0.05);border-radius:4px;">
                        <div style="width:${barWidth}%;height:100%;background:${barColor};border-radius:4px;transition:width 0.3s ease;"></div>
                        <div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:rgba(255,255,255,0.15);"></div>
                    </div>
                    <span style="min-width:50px;font-size:13px;font-weight:600;color:${textColor};">${pct}%</span>
                    <span style="min-width:40px;font-size:11px;color:#888;">(${b.n})</span>
                </div>
            `;
        }).join('');
    }

    // Peak patterns table
    const patternsEl = document.getElementById('rally-comfort-patterns');
    if (patternsEl && data.peak_patterns && data.peak_patterns.length > 0) {
        patternsEl.innerHTML = `
            <div style="font-size:13px;font-weight:600;margin-bottom:8px;color:var(--text-secondary);">
                Top patterns in ${data.peak_bucket}-ball rallies
            </div>
            <table class="data-table compact-table">
                <thead><tr><th>Pattern</th><th>N</th><th>Win Rate</th></tr></thead>
                <tbody>
                    ${data.peak_patterns.map(p => `
                        <tr>
                            <td>${formatShotType(p.pattern)}</td>
                            <td>${p.n}</td>
                            <td style="color:${p.pt_win_rate > 0.55 ? '#22c55e' : p.pt_win_rate < 0.45 ? '#ef4444' : 'inherit'}">${(p.pt_win_rate * 100).toFixed(1)}%</td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        `;
    } else if (patternsEl) {
        patternsEl.innerHTML = '';
    }
}

// ═══════════════════════════════════════════════════════════════════════
// PRESSURE-STATE TENDENCIES
// ═══════════════════════════════════════════════════════════════════════
const STATE_LABELS = {
    break_point_save: 'Break Point Save',
    break_point_convert: 'Break Point Convert',
    set_serving_ahead: 'Serving Ahead (Set)',
    set_serving_behind: 'Serving Behind (Set)',
    set_returning_ahead: 'Returning Ahead (Set)',
    set_returning_behind: 'Returning Behind (Set)',
    deuce_pressure: 'Deuce / 30-30',
    behind_in_game: 'Behind in Game',
    ahead_in_game: 'Ahead in Game',
    neutral: 'Neutral (Baseline)',
};

const STATE_ORDER = [
    'break_point_save', 'break_point_convert',
    'set_serving_ahead', 'set_serving_behind',
    'set_returning_ahead', 'set_returning_behind',
    'deuce_pressure', 'behind_in_game', 'ahead_in_game', 'neutral'
];

function renderPressureState(data) {
    const section = document.getElementById('pressure-state-section');
    if (!section) return;
    if (!FEATURE_FLAGS.ENABLE_PRESSURE_STATE || !data || !data.states || data.states.length === 0) {
        section.style.display = 'none';
        return;
    }

    section.style.display = 'block';
    const tbody = document.getElementById('pressure-state-tbody');
    if (!tbody) return;

    const baseline = data.baseline?.pt_win_rate ?? 0.5;

    // Sort by defined order
    const sorted = [...data.states].sort((a, b) => {
        const ai = STATE_ORDER.indexOf(a.label);
        const bi = STATE_ORDER.indexOf(b.label);
        return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    });

    tbody.innerHTML = sorted.map(s => {
        const pct = (s.pt_win_rate * 100).toFixed(1);
        const delta = s.pt_win_rate - baseline;
        const deltaPct = (delta * 100).toFixed(1);
        let deltaColor = '#888';
        let deltaText = `${deltaPct > 0 ? '+' : ''}${deltaPct}%`;

        if (Math.abs(delta) <= 0.03) {
            deltaColor = '#888';
        } else if (delta > 0) {
            deltaColor = '#22c55e';
        } else {
            deltaColor = '#ef4444';
        }

        return `
            <tr>
                <td style="font-weight:500;">${STATE_LABELS[s.label] || s.label}</td>
                <td>${s.n}</td>
                <td style="font-weight:600;color:${delta > 0.03 ? '#22c55e' : delta < -0.03 ? '#ef4444' : 'inherit'}">${pct}%</td>
                <td style="color:${deltaColor};font-weight:500;">${deltaText}</td>
            </tr>
        `;
    }).join('');
}

// ═══════════════════════════════════════════════════════════════════════
// OPPONENT-SPECIFIC PATTERN SHIFTS
// ═══════════════════════════════════════════════════════════════════════
function renderOpponentShifts(data) {
    const section = document.getElementById('opponent-shifts-section');
    if (!section) return;
    if (!FEATURE_FLAGS.ENABLE_OPPONENT_SHIFT || !data || !data.segments || data.segments.length === 0) {
        section.style.display = 'none';
        return;
    }

    section.style.display = 'block';
    const grid = document.getElementById('opponent-shifts-grid');
    if (!grid) return;

    const segmentCount = data.segments.length;
    if (segmentCount === 1) {
        grid.style.gridTemplateColumns = '1fr';
    }

    grid.innerHTML = data.segments.map(seg => {
        const winPct = (seg.pt_win_rate * 100).toFixed(1);
        const deltaPct = (seg.delta * 100).toFixed(1);
        const deltaSign = seg.delta >= 0 ? '+' : '';

        const badgeColor = {
            amplified: 'background:#dcfce7;color:#166534;',
            weakened: 'background:#fee2e2;color:#991b1b;',
            holds: 'background:#f3f4f6;color:#6b7280;',
        };

        return `
            <div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:20px;">
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
                    <span style="font-weight:600;font-size:15px;">${seg.segment_label}</span>
                    <span style="font-size:12px;color:#888;">${seg.matches} matches · ${seg.points} pts</span>
                </div>
                <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
                    <span style="font-size:24px;font-weight:700;">${winPct}%</span>
                    <span style="font-size:13px;padding:3px 8px;border-radius:4px;${badgeColor[seg.status] || badgeColor.holds}">
                        ${deltaSign}${deltaPct}% · ${seg.status.charAt(0).toUpperCase() + seg.status.slice(1)}
                    </span>
                </div>
                <div style="font-size:12px;color:#888;">Point win rate in this segment</div>
            </div>
        `;
    }).join('');
}

init();
