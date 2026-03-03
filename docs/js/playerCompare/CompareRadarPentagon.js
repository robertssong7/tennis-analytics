// CompareRadarPentagon.js → Now Hexagon with 6 axes + overlaid radars

export function destroyRadar(canvasId) {
    if (window.CompareRadarChartInst && window.CompareRadarChartInst[canvasId]) {
        window.CompareRadarChartInst[canvasId].destroy();
        delete window.CompareRadarChartInst[canvasId];
    }
}

const RADAR_AXES = [
    { key: 'serve', label: 'Serve' },
    { key: 'ground_consistency', label: 'GS Consistency' },
    { key: 'aggression_efficiency', label: 'Aggression' },
    { key: 'volley_win', label: 'Net Game' },
    { key: 'break_point_defense', label: 'BP Defense' },
    { key: 'aggregate_consistency', label: 'Consistency' },
];

const AXIS_INFO = {
    serve: 'Serve Quality Index (SQI): 1st/2nd serve win %, free points, DF penalty',
    ground_consistency: 'Groundstroke Consistency: low unforced error rate on FH + BH',
    aggression_efficiency: 'Aggression Efficiency: winners minus unforced errors per shot',
    volley_win: 'Net Game: win rate on all net approaches (volleys, smashes)',
    break_point_defense: 'Break Point Defense: % of break points saved',
    aggregate_consistency: 'Match-to-Match Consistency: low variance in performance quality',
};

export function renderCompareRadar(canvasId, playerA, playerB) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;

    destroyRadar(canvasId);

    const countNonNull = (pct) => {
        if (!pct) return 0;
        return RADAR_AXES.filter(a => pct[a.key] !== null && pct[a.key] !== undefined).length;
    };

    let canShowRadar = true;
    if (playerA && countNonNull(playerA.percentiles) < 3) canShowRadar = false;
    if (playerB && countNonNull(playerB.percentiles) < 3) canShowRadar = false;
    if (!playerA && !playerB) canShowRadar = false;

    const container = canvas.parentElement;
    if (!canShowRadar) {
        canvas.style.display = 'none';
        let msg = container.querySelector('.radar-error-msg');
        if (!msg) {
            msg = document.createElement('div');
            msg.className = 'radar-error-msg';
            msg.style.cssText = 'color:#94a3b8;text-align:center;padding:40px 20px;font-size:14px;';
            msg.innerText = "Not enough data to compare on radar.";
            container.appendChild(msg);
        } else {
            msg.style.display = 'block';
        }
        return;
    } else {
        canvas.style.display = 'block';
        const msg = container.querySelector('.radar-error-msg');
        if (msg) msg.style.display = 'none';
    }

    const labels = RADAR_AXES.map(a => a.label);

    const getRadarData = (p) => {
        if (!p || !p.percentiles) return RADAR_AXES.map(() => null);
        return RADAR_AXES.map(a => p.percentiles[a.key] ?? null);
    };

    const datasets = [];

    if (playerA) {
        datasets.push({
            label: playerA.lastName || 'Player A',
            data: getRadarData(playerA),
            backgroundColor: 'rgba(56, 189, 248, 0.15)',
            borderColor: '#38bdf8',
            pointBackgroundColor: '#38bdf8',
            pointBorderColor: '#fff',
            pointHoverBackgroundColor: '#fff',
            pointHoverBorderColor: '#38bdf8',
            borderWidth: 2.5,
            pointRadius: 4,
        });
    }

    if (playerB) {
        datasets.push({
            label: playerB.lastName || 'Player B',
            data: getRadarData(playerB),
            backgroundColor: 'rgba(244, 63, 94, 0.15)',
            borderColor: '#f43f5e',
            pointBackgroundColor: '#f43f5e',
            pointBorderColor: '#fff',
            pointHoverBackgroundColor: '#fff',
            pointHoverBorderColor: '#f43f5e',
            borderWidth: 2.5,
            pointRadius: 4,
        });
    }

    if (!window.CompareRadarChartInst) window.CompareRadarChartInst = {};

    window.CompareRadarChartInst[canvasId] = new Chart(canvas, {
        type: 'radar',
        data: { labels, datasets },
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
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: {
                        color: '#f8fafc',
                        font: { family: 'Inter, sans-serif', size: 12, weight: '500' },
                        padding: 16,
                        usePointStyle: true,
                        pointStyle: 'circle',
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(15, 23, 42, 0.95)',
                    titleFont: { family: 'Inter', size: 13 },
                    bodyFont: { family: 'Inter', size: 12 },
                    padding: 12,
                    cornerRadius: 6,
                    displayColors: true,
                    callbacks: {
                        label: (ctx) => `${ctx.dataset.label}: ${ctx.raw !== null ? ctx.raw + '/100' : 'N/A'}`
                    }
                }
            }
        }
    });

    // Info button
    let infoBtn = container.querySelector('.radar-info-btn');
    if (!infoBtn) {
        infoBtn = document.createElement('div');
        infoBtn.className = 'radar-info-btn';
        infoBtn.innerHTML = 'i';
        infoBtn.title = Object.entries(AXIS_INFO).map(([k, v]) => `${v}`).join('\n\n');
        Object.assign(infoBtn.style, {
            position: 'absolute', top: '10px', right: '10px',
            width: '24px', height: '24px', borderRadius: '50%',
            background: '#334155', color: '#cbd5e1',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: '12px', fontWeight: 'bold', cursor: 'help',
            border: '1px solid #475569', zIndex: '5',
        });
        container.style.position = 'relative';
        container.appendChild(infoBtn);
    }
}
