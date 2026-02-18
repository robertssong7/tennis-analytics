/* ═══════════════════════════════════════════════════════════════════════
   TennisIQ — Frontend Application (Static + Dynamic Mode)
   ═══════════════════════════════════════════════════════════════════════ */

// ─── Mode Detection ──────────────────────────────────────────────────
// If data/players.json exists → static mode (GitHub Pages)
// Otherwise → dynamic API mode (Express server)
let STATIC_MODE = false;
let allPlayers = [];

// ─── State ───────────────────────────────────────────────────────────
let currentPlayer = null;
let showLowConfidence = false;

// ─── DOM Refs ────────────────────────────────────────────────────────
const searchInput = document.getElementById('player-search');
const searchDropdown = document.getElementById('search-dropdown');
const dashboard = document.getElementById('dashboard');
const emptyState = document.getElementById('empty-state');
const loadingOverlay = document.getElementById('loading-overlay');
const lowConfToggle = document.getElementById('low-conf-toggle');

// ─── Helpers ─────────────────────────────────────────────────────────
function formatShotType(s) {
    return s.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase());
}

function formatPct(v) { return (v * 100).toFixed(1) + '%'; }

function effHTML(val) {
    const pct = Math.min(Math.abs(val) * 100, 100);
    const cls = val >= 0 ? 'positive' : 'negative';
    return `
    <div class="eff-bar">
      <div class="eff-bar-track">
        <div class="eff-bar-fill ${cls}" style="width:${pct}%"></div>
      </div>
      <span class="eff-value ${cls}">${(val * 100).toFixed(1)}%</span>
    </div>`;
}

function badgeHTML(conf) {
    const labels = { high: '✓ N≥30', low: '⚠ N≥10', insufficient: '✗ Low N' };
    return `<span class="badge ${conf}">${labels[conf] || conf}</span>`;
}

function ciHTML(ci) {
    return `<span class="ci-display"><span class="ci-bracket">[</span>${(ci.lower * 100).toFixed(1)}–${(ci.upper * 100).toFixed(1)}%<span class="ci-bracket">]</span></span>`;
}

function rowClass(conf) {
    if (conf === 'insufficient') return 'row-hidden';
    if (conf === 'low' && !showLowConfidence) return 'row-low-conf row-hidden';
    if (conf === 'low') return 'row-low-conf';
    return '';
}

function showLoading() { loadingOverlay.classList.remove('hidden'); }
function hideLoading() { loadingOverlay.classList.add('hidden'); }

// ─── Data Fetching (dual mode) ──────────────────────────────────────
async function fetchJSON(url) {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`Fetch error: ${resp.status}`);
    return resp.json();
}

async function fetchPlayerData(playerName, endpoint) {
    if (STATIC_MODE) {
        return fetchJSON(`data/players/${playerName}/${endpoint}.json`);
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
    f.minN = showLowConfidence ? 10 : 30;
    return f;
}

// ═══════════════════════════════════════════════════════════════════════
// INITIALIZATION — detect static/dynamic mode
// ═══════════════════════════════════════════════════════════════════════
async function init() {
    try {
        allPlayers = await fetchJSON('data/players.json');
        STATIC_MODE = true;
        console.log(`TennisIQ: Static mode — ${allPlayers.length} players loaded`);
        // In static mode, hide surface filter (data is pre-computed without filters)
        document.getElementById('filter-bar').style.display = 'none';
    } catch {
        STATIC_MODE = false;
        console.log('TennisIQ: Dynamic API mode');
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
                ).slice(0, 20);
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
    currentPlayer = name;
    showLoading();
    emptyState.classList.add('hidden');

    try {
        const [coverage, patterns, serve, serveOne, compare, directions, insights] =
            await Promise.all([
                fetchPlayerData(name, 'coverage'),
                fetchPlayerData(name, 'patterns'),
                fetchPlayerData(name, 'serve'),
                fetchPlayerData(name, 'serve-plus-one'),
                fetchPlayerData(name, 'compare'),
                fetchPlayerData(name, 'direction-patterns'),
                fetchPlayerData(name, 'insights'),
            ]);

        renderPlayerHeader(name, coverage);
        renderInsights(insights);
        renderCoverage(coverage);
        renderPatterns(patterns);
        renderServe(serve);
        renderServeOne(serveOne);
        renderDirections(directions);
        renderCompare(compare);

        dashboard.classList.remove('hidden');
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
// RENDER: Insights
// ═══════════════════════════════════════════════════════════════════════
function renderInsights(insights) {
    const grid = document.getElementById('insights-grid');
    if (insights.length === 0) {
        grid.innerHTML = '<p style="color:var(--text-muted);padding:12px">No significant pattern divergences found between wins and losses.</p>';
        return;
    }
    grid.innerHTML = insights.map(i => `
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
    const tbody = document.getElementById('patterns-tbody');
    tbody.innerHTML = patterns.map(p => `
    <tr class="${rowClass(p.confidence)}">
      <td style="font-weight:600">${formatShotType(p.shotType)}</td>
      <td class="num">${p.total}</td>
      <td class="num" style="color:var(--green)">${p.winners}</td>
      <td class="num" style="color:var(--orange)">${p.unforcedErrors}</td>
      <td class="num" style="color:var(--red)">${p.forcedErrors}</td>
      <td class="num">${formatPct(p.winnerRate)}</td>
      <td class="num">${formatPct(p.errorRate)}</td>
      <td>${effHTML(p.effectiveness)}</td>
      <td>${ciHTML(p.winnerCI)}</td>
      <td>${badgeHTML(p.confidence)}</td>
    </tr>
  `).join('');
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
      <td class="num" style="color:var(--green)">${s.winners}</td>
      <td class="num" style="color:var(--red)">${s.errors}</td>
      <td class="num">${s.inPlay}</td>
      <td class="num">${formatPct(s.winnerRate)}</td>
      <td>${ciHTML(s.winnerCI)}</td>
      <td>${badgeHTML(s.confidence)}</td>
    </tr>
  `).join('');
}

// ═══════════════════════════════════════════════════════════════════════
// RENDER: Serve + 1
// ═══════════════════════════════════════════════════════════════════════
function renderServeOne(serveOne) {
    const tbody = document.getElementById('serve-one-tbody');
    tbody.innerHTML = serveOne.map(s => `
    <tr class="${rowClass(s.confidence)}">
      <td style="font-weight:600">${s.serveDir.replace('_', ' ')} → ${formatShotType(s.responseType)}</td>
      <td class="num">${s.total}</td>
      <td class="num">${formatPct(s.winnerRate)}</td>
      <td class="num">${formatPct(s.errorRate)}</td>
      <td>${ciHTML(s.winnerCI)}</td>
      <td>${badgeHTML(s.confidence)}</td>
    </tr>
  `).join('');
}

// ═══════════════════════════════════════════════════════════════════════
// RENDER: Direction Patterns
// ═══════════════════════════════════════════════════════════════════════
function renderDirections(directions) {
    const tbody = document.getElementById('direction-tbody');
    tbody.innerHTML = directions.map(d => `
    <tr class="${rowClass(d.confidence)}">
      <td style="font-weight:600">${formatShotType(d.shotType)}</td>
      <td>${d.direction.replace('_', ' ')}</td>
      <td class="num">${d.total}</td>
      <td class="num">${formatPct(d.winnerRate)}</td>
      <td class="num">${formatPct(d.errorRate)}</td>
      <td>${effHTML(d.effectiveness)}</td>
      <td>${ciHTML(d.winnerCI)}</td>
      <td>${badgeHTML(d.confidence)}</td>
    </tr>
  `).join('');
}

// ═══════════════════════════════════════════════════════════════════════
// RENDER: Win vs Loss Comparison
// ═══════════════════════════════════════════════════════════════════════
function renderCompare(compare) {
    renderCompareTable('compare-win-tbody', compare.wins);
    renderCompareTable('compare-loss-tbody', compare.losses);
}

function renderCompareTable(tbodyId, data) {
    const tbody = document.getElementById(tbodyId);
    tbody.innerHTML = data.map(p => `
    <tr class="${rowClass(p.confidence)}">
      <td style="font-weight:600">${formatShotType(p.shotType)}</td>
      <td class="num">${p.total}</td>
      <td class="num">${formatPct(p.winnerRate)}</td>
      <td class="num">${formatPct(p.errorRate)}</td>
      <td>${effHTML(p.effectiveness)}</td>
      <td>${ciHTML(p.winnerCI)}</td>
    </tr>
  `).join('');
}

// ═══════════════════════════════════════════════════════════════════════
// EVENTS
// ═══════════════════════════════════════════════════════════════════════

// Low confidence toggle
lowConfToggle.addEventListener('change', () => {
    showLowConfidence = lowConfToggle.checked;
    if (currentPlayer) loadPlayer(currentPlayer);
});

// Apply filters (dynamic mode only)
document.getElementById('filter-apply').addEventListener('click', () => {
    if (currentPlayer) loadPlayer(currentPlayer);
});

// Reset filters
document.getElementById('filter-reset').addEventListener('click', () => {
    document.getElementById('filter-surface').value = '';
    document.getElementById('filter-date-from').value = '';
    document.getElementById('filter-date-to').value = '';
    document.getElementById('filter-serve-num').value = '';
    document.getElementById('filter-side').value = '';
    if (currentPlayer) loadPlayer(currentPlayer);
});

// Enter key in search
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
// BOOT
// ═══════════════════════════════════════════════════════════════════════
init();
