/* ═══════════════════════════════════════════════════════════════════════
   TennisIQ — Frontend Application (Static + Dynamic Mode)
   ═══════════════════════════════════════════════════════════════════════ */

// ─── Mode Detection ──────────────────────────────────────────────────
let STATIC_MODE = false;
let allPlayers = [];

// ─── State ───────────────────────────────────────────────────────────
const FEATURE_FLAGS = { showScoutReport: false };
let expandedTables = {};
let currentPlayer = null;

// ─── DOM Refs ────────────────────────────────────────────────────────
const searchInput = document.getElementById('player-search');
const searchDropdown = document.getElementById('search-dropdown');
const dashboard = document.getElementById('dashboard');
const emptyState = document.getElementById('empty-state');
const loadingOverlay = document.getElementById('loading-overlay');

// ─── Helpers ─────────────────────────────────────────────────────────
function formatShotType(s) {
    return s.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase());
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
        // pattern-inference may not exist for all users if generating static before update
        try {
            return await fetchJSON(`data/players/${playerName}/${endpoint}.json`);
        } catch {
            return endpoint === 'pattern-inference' ? { winning: [], losing: [] } : [];
        }
    }
    const params = endpoint === 'coverage' || endpoint === 'insights' ? {} : getFilters();
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
// LOAD PLAYER DATA
// ═══════════════════════════════════════════════════════════════════════
async function loadPlayer(name) {
    expandedTables = {};
    currentPlayer = name;
    showLoading();
    emptyState.classList.add('hidden');

    try {
        const [coverage, patterns, serve, serveOne, compare, directions, insights, inference] =
            await Promise.all([
                fetchPlayerData(name, 'coverage'),
                fetchPlayerData(name, 'patterns'),
                fetchPlayerData(name, 'serve'),
                fetchPlayerData(name, 'serve-plus-one'),
                fetchPlayerData(name, 'compare'),
                fetchPlayerData(name, 'direction-patterns'),
                FEATURE_FLAGS.showScoutReport ? fetchPlayerData(name, 'insights') : Promise.resolve([]),
                fetchPlayerData(name, 'pattern-inference')
            ]);

        renderPlayerHeader(name, coverage);

        const insightsSection = document.getElementById('insights-section');
        if (FEATURE_FLAGS.showScoutReport) {
            if (insightsSection) insightsSection.style.display = 'block';
            renderInsights(insights);
        } else {
            if (insightsSection) insightsSection.style.display = 'none';
        }

        renderCoverage(coverage);
        renderPatterns(patterns);
        renderServe(serve);
        renderServeOne(serveOne);
        renderDirections(directions);
        renderCompare(compare);
        renderInference(inference);

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
    document.getElementById('player-meta').textContent =
        `${t.total_matches} charted matches · ${Number(t.total_points).toLocaleString()} points tracked`;
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
    document.getElementById('coverage-totals').innerHTML = `
    <div class="cov-stat"><span class="value">${t.total_matches}</span><span class="label">Matches</span></div>
    <div class="cov-stat"><span class="value">${Number(t.total_points).toLocaleString()}</span><span class="label">Points</span></div>
  `;

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
      <td style="font-weight:600">${seq.sequence.map(s => s.replace(/_/g, ' ')).join(' → ')}</td>
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

init();
