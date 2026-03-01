export function destroyRadar(canvasId) {
    if (window.CompareRadarChartInst && window.CompareRadarChartInst[canvasId]) {
        window.CompareRadarChartInst[canvasId].destroy();
        delete window.CompareRadarChartInst[canvasId];
    }
}

export function renderCompareRadar(canvasId, playerA, playerB) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;

    destroyRadar(canvasId);

    // "radar rule: if either player has < 4 non-null radar axes, hide radar"
    const countNonNull = (radar) => Object.values(radar).filter(v => v !== null && v !== undefined).length;

    let canShowRadar = true;
    if (playerA && countNonNull(playerA.radar) < 4) canShowRadar = false;
    if (playerB && countNonNull(playerB.radar) < 4) canShowRadar = false;
    if (!playerA && !playerB) canShowRadar = false;

    const container = canvas.parentElement;
    if (!canShowRadar) {
        canvas.style.display = 'none';
        let msg = container.querySelector('.radar-error-msg');
        if (!msg) {
            msg = document.createElement('div');
            msg.className = 'radar-error-msg';
            msg.style.color = '#94a3b8';
            msg.style.textAlign = 'center';
            msg.style.padding = '40px 20px';
            msg.style.fontSize = '14px';
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

    const labels = ['Serve', 'Forehand', 'Backhand', 'Pace', 'Consistency'];

    const getRadarData = (p) => {
        if (!p) return [null, null, null, null, null];
        return [
            p.radar.serve,
            p.radar.forehand,
            p.radar.backhand,
            p.radar.pace,
            p.radar.consistency
        ];
    };

    const datasets = [];

    if (playerA) {
        datasets.push({
            label: playerA.lastName,
            data: getRadarData(playerA),
            backgroundColor: 'rgba(56, 189, 248, 0.2)', // Blue
            borderColor: '#38bdf8',
            pointBackgroundColor: '#38bdf8',
            pointBorderColor: '#fff',
            pointHoverBackgroundColor: '#fff',
            pointHoverBorderColor: '#38bdf8',
            borderWidth: 2
        });
    }

    if (playerB) {
        datasets.push({
            label: playerB.lastName,
            data: getRadarData(playerB),
            backgroundColor: 'rgba(244, 63, 94, 0.2)', // Rose / Red
            borderColor: '#f43f5e',
            pointBackgroundColor: '#f43f5e',
            pointBorderColor: '#fff',
            pointHoverBackgroundColor: '#fff',
            pointHoverBorderColor: '#f43f5e',
            borderWidth: 2
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
                    angleLines: { color: 'rgba(255, 255, 255, 0.1)' },
                    grid: { color: 'rgba(255, 255, 255, 0.1)' },
                    pointLabels: {
                        color: '#94a3b8',
                        font: { family: 'Inter', size: 11, weight: '600' }
                    },
                    ticks: {
                        display: false,
                        min: 0,
                        max: 100,
                        stepSize: 20
                    }
                }
            },
            plugins: {
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: { color: '#f8fafc', font: { family: 'Inter', size: 12 } }
                },
                tooltip: {
                    backgroundColor: 'rgba(15, 23, 42, 0.9)',
                    titleFont: { family: 'Inter', size: 13 },
                    bodyFont: { family: 'Inter', size: 12 },
                    padding: 10,
                    cornerRadius: 4,
                    displayColors: true,
                    callbacks: {
                        label: function (context) {
                            return `${context.dataset.label}: ${context.raw !== null ? context.raw : 'N/A'}`;
                        }
                    }
                }
            }
        }
    });

    // Add info 'i' button popover if not created
    let infoBtn = container.querySelector('.radar-info-btn');
    if (!infoBtn) {
        infoBtn = document.createElement('div');
        infoBtn.className = 'radar-info-btn';
        infoBtn.innerHTML = 'i';
        infoBtn.title = "Serve: Tour percentile for serve dominance (or Serve+1 proxy)\\nForehand: Tour percentile of forehand pattern effectiveness\\nBackhand: Tour percentile of backhand pattern effectiveness\\nPace: Proxy percentile based on defensive problem-solving speeds\\nConsistency: Tour percentile for stability of performance\\n\\nAll values are percentiles vs tour (All surfaces).";

        // Basic absolute positioning inline style
        Object.assign(infoBtn.style, {
            position: 'absolute',
            top: '10px',
            right: '10px',
            width: '24px',
            height: '24px',
            borderRadius: '50%',
            background: '#334155',
            color: '#cbd5e1',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '12px',
            fontWeight: 'bold',
            cursor: 'help',
            border: '1px solid #475569'
        });

        container.style.position = 'relative'; // Ensure button anchors
        container.appendChild(infoBtn);
    }
}
