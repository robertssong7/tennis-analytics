// docs/js/playerRadar/RadarComponent.js
// v2: Uses pre-computed percentiles from player_percentiles_all.json
// Hexagon radar with 6 axes

let cachedPercentiles = null;
let radarChartInstance = null;

async function loadPercentiles() {
    if (cachedPercentiles) return cachedPercentiles;
    const resp = await fetch('./data/player_percentiles_all.json');
    cachedPercentiles = await resp.json();
    return cachedPercentiles;
}

const RADAR_AXES = [
    { key: 'serve', label: 'Serve', tooltip: 'Serve Quality Index: weighted combination of 1st serve win %, 2nd serve win %, free point rate, minus double fault rate. Percentile vs all charted players.' },
    { key: 'ground_consistency', label: 'GS Consistency', tooltip: 'Groundstroke Consistency: how rarely a player makes unforced errors per groundstroke attempt (FH + BH combined). Percentile vs Tour.' },
    { key: 'aggression_efficiency', label: 'Aggression', tooltip: 'Aggression Efficiency: winner rate minus unforced error rate on groundstrokes. Higher = more damage with fewer mistakes. Percentile vs Tour.' },
    { key: 'volley_win', label: 'Net Game', tooltip: 'Net Game: win rate when approaching the net, including volleys, smashes, and overheads. Percentile vs Tour.' },
    { key: 'break_point_defense', label: 'BP Defense', tooltip: 'Break Point Defense: how often a player saves break points faced on serve. Percentile vs Tour.' },
    { key: 'aggregate_consistency', label: 'Consistency', tooltip: 'Match-to-Match Consistency: stability of performance quality across all matches. Lower variance = higher score. Percentile vs Tour.' },
];

function makePlayerId(name) {
    return name.trim().toLowerCase()
        .replace(/[^\w\s]/g, '')
        .replace(/\s+/g, '_');
}

export async function renderRadar(containerId, dataPackages) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const percentiles = await loadPercentiles();

    // Get player name from the page
    const playerName = window.currentPlayer || document.querySelector('.player-name')?.textContent;
    if (!playerName) return;

    const playerId = makePlayerId(playerName);
    const playerPct = percentiles[playerId];

    if (!playerPct) {
        container.innerHTML = `<div style="color: #94a3b8; text-align: center; padding: 40px 20px; font-size: 14px;">
            Percentile data not available for this player.</div>`;
        return;
    }

    // Count non-null axes
    const nonNull = RADAR_AXES.filter(a => playerPct[a.key] !== null && playerPct[a.key] !== undefined).length;
    if (nonNull < 3) {
        container.innerHTML = `<div style="color: #94a3b8; text-align: center; padding: 40px 20px; font-size: 14px;">
            Not enough data for radar (${nonNull}/6 axes available).</div>`;
        return;
    }

    // Build HTML
    container.innerHTML = `
        <div style="position: relative; padding: 16px;">
            <div class="radar-info-btn" title="" style="
                position: absolute; top: 10px; right: 10px;
                width: 24px; height: 24px; border-radius: 50%;
                background: #334155; color: #cbd5e1;
                display: flex; align-items: center; justify-content: center;
                font-size: 12px; font-weight: bold; cursor: help;
                border: 1px solid #475569; z-index: 5;
            ">i</div>
            <h3 style="margin: 0 0 12px 0; color: #f8fafc; font-size: 16px; font-weight: 600;">
                Tour Percentile Radar
            </h3>
            <div style="height: 300px;">
                <canvas id="player-radar-canvas"></canvas>
            </div>
        </div>
    `;

    // Setup info tooltip
    const infoBtn = container.querySelector('.radar-info-btn');
    infoBtn.title = RADAR_AXES.map(a => `${a.label}: ${a.tooltip}`).join('\n\n');

    // Render Chart.js radar
    const canvas = document.getElementById('player-radar-canvas');
    if (radarChartInstance) radarChartInstance.destroy();

    const labels = RADAR_AXES.map(a => a.label);
    const data = RADAR_AXES.map(a => playerPct[a.key] ?? null);

    radarChartInstance = new Chart(canvas, {
        type: 'radar',
        data: {
            labels,
            datasets: [{
                label: playerName,
                data,
                backgroundColor: 'rgba(56, 189, 248, 0.2)',
                borderColor: '#38bdf8',
                pointBackgroundColor: '#38bdf8',
                pointBorderColor: '#fff',
                pointHoverBackgroundColor: '#fff',
                pointHoverBorderColor: '#38bdf8',
                borderWidth: 2.5,
                pointRadius: 4,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                r: {
                    angleLines: { color: 'rgba(255, 255, 255, 0.08)' },
                    grid: { color: 'rgba(255, 255, 255, 0.08)' },
                    pointLabels: {
                        color: '#cbd5e1',
                        font: { family: 'Inter, sans-serif', size: 11, weight: '600' }
                    },
                    ticks: { display: false },
                    min: 0,
                    max: 100,
                    beginAtZero: true,
                }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(15, 23, 42, 0.95)',
                    titleFont: { family: 'Inter', size: 13 },
                    bodyFont: { family: 'Inter', size: 12 },
                    padding: 12,
                    cornerRadius: 6,
                    callbacks: {
                        label: (ctx) => `${ctx.raw !== null ? ctx.raw + '/100' : 'N/A'}`
                    }
                }
            }
        }
    });
}

export { RADAR_AXES, loadPercentiles, makePlayerId };
