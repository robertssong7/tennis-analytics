// docs/js/playerRadar/RadarComponent.js
import { computeRawScores } from './radarModel.js';
import { calculatePercentile } from './percentiles.js';

let cachedDistributions = null;
let radarChartInstance = null;

async function loadDistributions() {
    if (cachedDistributions) return cachedDistributions;
    try {
        const resp = await fetch('data/tourDistributionsAll.json');
        if (!resp.ok) throw new Error('Distributions not found');
        cachedDistributions = await resp.json();
        return cachedDistributions;
    } catch (e) {
        console.warn('Radar Module: Failed to load distributions. Percentiles will default to 50.');
        return null;
    }
}

const AXIS_CONFIG = [
    { key: 'serveStrengthProfile', label: 'Serve Strength Profile', invert: false, tooltip: 'Tour percentile for serve dominance using expected service points won (and unreturned/DF rates when needed).' },
    { key: 'servePlusOneAdvantage', label: 'Serve+1 Advantage', invert: false, tooltip: 'Tour percentile for how Serve→Response patterns outperform player baseline.' },
    { key: 'defensiveProblemSolving', label: 'Defensive Problem-Solving', invert: false, tooltip: 'Tour percentile for slice/lob pattern value vs baseline.' },
    { key: 'finishingConversion', label: 'Finishing Conversion', invert: false, tooltip: 'Tour percentile for volley/smash pattern value vs baseline.' },
    { key: 'exploitability', label: 'Shot Balance', invert: true, tooltip: 'Tour percentile for balance between LEFT vs RIGHT effectiveness; more balanced ranks higher.' },
    { key: 'patternStability', label: 'Pattern Stability', invert: false, tooltip: 'Tour percentile for consistency of pattern performance; less volatility ranks higher.' }
];

export async function renderRadar(containerId, dataPackages) {
    const container = document.getElementById(containerId);
    if (!container) return;

    // Load static arrays
    const dist = await loadDistributions();

    // Evaluate Raw Scores
    const rawScores = computeRawScores(dataPackages);
    if (rawScores.serveStrengthProfile === null) {
        console.warn('Serve strength unavailable: missing point-level serve outcome fields.');
    }

    // Determine Percentiles for all 6 axes
    let validCount = 0;
    const labels = [];
    const dataPoints = [];
    const formattedData = [];

    for (const axis of AXIS_CONFIG) {
        labels.push(axis.label);
        const raw = rawScores[axis.key];
        let pct = null;

        if (raw !== null && dist && dist[axis.key]) {
            pct = calculatePercentile(raw, dist[axis.key], axis.invert);
            validCount++;
        }

        dataPoints.push(pct);
        formattedData.push({ label: axis.label, raw, pct, tooltip: axis.tooltip });
    }

    // PRD: "If fewer than 4 axes available: hide radar and show 'Not enough data for this filter context.'"
    if (validCount < 4) {
        container.innerHTML = `
            <section class="card module-card" id="radar-section">
                <div class="header-text" style="padding: 16px;">
                    <h3>🕸️ Tour-Percentile Player Radar</h3>
                    <p style="color:var(--text-muted); font-size:14px; margin-top:8px;">Not enough data for this filter context.</p>
                </div>
            </section>
        `;
        return;
    }

    // Inject DOM structural skeleton
    container.innerHTML = `
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 24px;">
            <section class="card module-card" id="radar-section" style="overflow:visible; height: 100%;">
                <div style="display:flex; justify-content:space-between; align-items:center; padding: 16px 20px 0;">
                    <div class="header-text">
                        <h3>🕸️ Tour-Percentile Player Radar</h3>
                        <span class="card-subtitle">0-100 Percentiles against Tour Baseline</span>
                    </div>
                    <div class="info-popover-container" style="position:relative;">
                        <button id="radar-info-btn" style="background:#f1f5f9; border:none; width:28px; height:28px; border-radius:50%; font-weight:bold; color:var(--text-muted); cursor:pointer;">i</button>
                        <div id="radar-info-tooltip" style="display:none; position:absolute; right:0; top:36px; background:white; border:1px solid #e2e8f0; border-radius:8px; padding:16px; width:300px; box-shadow:0 10px 15px -3px rgba(0,0,0,0.1); z-index:100;">
                            <ul style="list-style:none; padding:0; margin:0; font-size:12px; color:#475569; display:flex; flex-direction:column; gap:8px;">
                                ${formattedData.map(d => `<li><strong>${d.label}:</strong> ${d.tooltip}</li>`).join('')}
                            </ul>
                        </div>
                    </div>
                </div>
                <div class="card-content" style="display:flex; justify-content:center; align-items:center; padding: 0 20px 20px;">
                    <div style="width: 100%; max-width: 500px; position:relative;">
                        <canvas id="player-radar-canvas"></canvas>
                    </div>
                </div>
            </section>
            
            <!-- Empty space for future graph -->
            <section class="card module-card" style="display:flex; justify-content:center; align-items:center; border: 2px dashed rgba(255,255,255,0.1); background: transparent; height: 100%; box-shadow: none;">
                <span style="color: var(--text-muted); font-size: 14px; font-weight: 500;">Future Graph Space</span>
            </section>
        </div>
    `;

    // Setup Info Tooltip
    const infoBtn = document.getElementById('radar-info-btn');
    const infoTooltip = document.getElementById('radar-info-tooltip');

    infoBtn.addEventListener('mouseenter', () => infoTooltip.style.display = 'block');
    infoBtn.addEventListener('mouseleave', () => infoTooltip.style.display = 'none');

    // Render Chart.js
    const ctx = document.getElementById('player-radar-canvas').getContext('2d');

    if (radarChartInstance) radarChartInstance.destroy();

    // Make undefined map to 0 explicitly but retain formatted null flag for tooltips 
    const drawData = dataPoints.map(p => p === null ? null : p);
    const tourAverageData = dataPoints.map(p => p === null ? null : 50);

    radarChartInstance = new window.Chart(ctx, {
        type: 'radar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Tour Average',
                    data: tourAverageData,
                    backgroundColor: 'rgba(255, 255, 255, 0.05)',
                    borderColor: 'rgba(255, 255, 255, 0.2)',
                    borderWidth: 1,
                    borderDash: [5, 5],
                    pointRadius: 0,
                    pointHoverRadius: 0,
                    fill: true
                },
                {
                    label: 'Player Percentile',
                    data: drawData,
                    backgroundColor: 'rgba(56, 189, 248, 0.2)', // light blue
                    borderColor: 'rgba(2, 132, 199, 1)',       // stronger blue
                    pointBackgroundColor: 'rgba(2, 132, 199, 1)',
                    pointBorderColor: '#fff',
                    pointHoverBackgroundColor: '#fff',
                    pointHoverBorderColor: 'rgba(2, 132, 199, 1)',
                    borderWidth: 2,
                    fill: true
                }
            ]
        },
        options: {
            spanGaps: true, // Connect lines over missing null axes (e.g., Tier C serve)
            responsive: true,
            scales: {
                r: {
                    angleLines: { color: 'rgba(255, 255, 255, 0.15)' },
                    grid: { color: 'rgba(255, 255, 255, 0.1)' },
                    min: 0,
                    max: 100,
                    ticks: {
                        stepSize: 25,
                        display: false // hides the numbers like 25, 50, 75 along the axis
                    },
                    pointLabels: {
                        font: { family: "'Inter', sans-serif", size: 12, weight: '500' },
                        color: '#94a3b8'
                    }
                }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            if (context.raw === null) return ' — (Insufficient Data)';
                            return ` ${context.dataset.label}: ${context.raw}th Percentile`;
                        }
                    }
                }
            }
        }
    });
}
