/* ═══════════════════════════════════════════════════════════════════════
   Court Speed Dashboard (Home Screen)
   Displays CPI, Ace Rates, and Rally Lengths modeled after courtspeed.com
   ═══════════════════════════════════════════════════════════════════════ */

let courtSpeedData = [];
let currentSurfaceFilter = 'All';

// Retrieve color mapping for Court Pace Index
function getCPICategory(cpi) {
    if (cpi < 30) return { label: 'Slow', color: '#ef4444', bg: 'rgba(239, 68, 68, 0.15)' }; // Red
    if (cpi >= 30 && cpi <= 34.9) return { label: 'Medium Slow', color: '#f97316', bg: 'rgba(249, 115, 22, 0.15)' }; // Orange
    if (cpi >= 35 && cpi <= 39.9) return { label: 'Medium', color: '#eab308', bg: 'rgba(234, 179, 8, 0.15)' }; // Yellow
    if (cpi >= 40 && cpi <= 44.9) return { label: 'Medium Fast', color: '#3b82f6', bg: 'rgba(59, 130, 246, 0.15)' }; // Blue
    return { label: 'Fast', color: '#22c55e', bg: 'rgba(34, 197, 94, 0.15)' }; // Green (> 44)
}

export async function mountCourtSpeedDashboard(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    try {
        const resp = await fetch('data/court_speeds.json');
        if (!resp.ok) throw new Error('Failed to load court speeds json');
        courtSpeedData = await resp.json();
    } catch (err) {
        console.error("CourtSpeedDashboard Error:", err);
        container.innerHTML = '<div style="color:#94a3b8; padding:20px; text-align:center;">Speed data unavailable.</div>';
        return;
    }

    container.innerHTML = `
        <div class="court-speed-header" style="display:flex; justify-content:space-between; align-items:flex-end; margin-bottom: 16px; flex-wrap: wrap; gap: 12px;">
            <div>
                <div style="font-size:16px;color:#38bdf8;text-transform:uppercase;letter-spacing:1px;font-weight:800;margin-bottom:6px;">Tour Analytics</div>
                <div style="font-size:24px;font-weight:900;color:#0f172a;">Court Speed & Conditions</div>
            </div>
            <div id="court-speed-surface-toggle" style="background:#fff; border-radius:12px; padding:4px; box-shadow:0 4px 12px rgba(0,0,0,0.05);"></div>
        </div>
        
        <div id="court-speed-content" style="background:#0f172a; border-radius:12px; padding:20px; box-shadow:0 10px 25px rgba(0,0,0,0.1); border:1px solid #1e293b;"></div>
        
        <div class="cpi-legend" style="display:flex; align-items:center; justify-content:center; gap: 8px; margin-top: 16px; flex-wrap:wrap; font-size:11px; font-weight:600; color:#64748b;">
            <div style="display:flex; align-items:center; gap:4px;"><div style="width:12px;height:12px;border-radius:3px;background:#ef4444;"></div> &lt;30 Slow</div>
            <div style="display:flex; align-items:center; gap:4px;"><div style="width:12px;height:12px;border-radius:3px;background:#f97316;"></div> 30-34 Med-Slow</div>
            <div style="display:flex; align-items:center; gap:4px;"><div style="width:12px;height:12px;border-radius:3px;background:#eab308;"></div> 35-39 Medium</div>
            <div style="display:flex; align-items:center; gap:4px;"><div style="width:12px;height:12px;border-radius:3px;background:#3b82f6;"></div> 40-44 Med-Fast</div>
            <div style="display:flex; align-items:center; gap:4px;"><div style="width:12px;height:12px;border-radius:3px;background:#22c55e;"></div> &gt;44 Fast</div>
        </div>
    `;

    // Mount Surface Toggle
    try {
        const { createSurfaceToggle } = await import('../SurfaceToggle.js?v=cs2');
        createSurfaceToggle('court-speed-surface-toggle', (newSurface) => {
            currentSurfaceFilter = newSurface === 'all' ? 'All' : newSurface.charAt(0).toUpperCase() + newSurface.slice(1).toLowerCase();
            renderContent();
        });
    } catch (err) {
        console.error("Failed to mount surface toggle for CourtSpeedDashboard:", err);
    }

    renderContent();
}

function renderContent() {
    const content = document.getElementById('court-speed-content');
    if (!content) return;

    let filtered = courtSpeedData;
    if (currentSurfaceFilter !== 'All') {
        filtered = courtSpeedData.filter(d => d.surface.toLowerCase() === currentSurfaceFilter.toLowerCase());
    }

    if (filtered.length === 0) {
        content.innerHTML = `<div style="color:#94a3b8; text-align:center; padding: 30px;">No speed data available for ${currentSurfaceFilter} courts.</div>`;
        return;
    }

    // Usually we map through tournaments, but since the component is focused we'll render each matching tournament.
    // For now we'll just stack them (or if 1, just show 1).
    let html = '';

    filtered.forEach(tourney => {
        const cpiInfo = getCPICategory(tourney.overallSpeedRating);

        // Build History Rows
        const tbodyRows = tourney.history.map(h => {
            const hCpiInfo = getCPICategory(h.cpi);
            return `
                <tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
                    <td style="padding:10px 12px; color:#cbd5e1; font-weight:600;">${tourney.tournament}</td>
                    <td style="padding:10px 12px; color:#94a3b8;">${h.year}</td>
                    <td style="padding:10px 12px; color:#f8fafc;">
                        <span style="background:${hCpiInfo.bg}; color:${hCpiInfo.color}; padding: 2px 8px; border-radius:4px; font-weight:800;">${h.cpi.toFixed(1)}</span>
                    </td>
                    <td style="padding:10px 12px; color:#94a3b8;">${h.aceRate}</td>
                    <td style="padding:10px 12px; color:#94a3b8;">${h.rallyLength}</td>
                </tr>
            `;
        }).join('');

        html += `
            <div style="margin-bottom: 24px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 16px; flex-wrap:wrap; gap:12px;">
                    <div>
                        <div style="display:inline-block; padding:2px 8px; border-radius:4px; background:rgba(255,255,255,0.1); color:#fff; font-size:11px; font-weight:bold; letter-spacing:0.5px; text-transform:uppercase; margin-bottom:6px;">${tourney.surface}</div>
                        <h3 style="margin:0; font-size:20px; color:#f8fafc;">${tourney.tournament}</h3>
                    </div>
                    <div style="text-align:right;">
                        <div style="font-size:12px; color:#94a3b8; margin-bottom:4px;">Average Pace (CPI)</div>
                        <div style="display:inline-block; background:${cpiInfo.bg}; color:${cpiInfo.color}; border: 1px solid ${cpiInfo.color}; padding: 4px 12px; border-radius:6px; font-weight:800; font-size:18px;">
                            ${tourney.overallSpeedRating.toFixed(1)} • ${cpiInfo.label}
                        </div>
                    </div>
                </div>

                <!-- Aggregates Row -->
                <div style="display:grid; grid-template-columns: repeat(3, 1fr); gap:12px; margin-bottom: 20px;">
                    <div style="background: rgba(255,255,255,0.03); padding:12px; border-radius:8px; border: 1px solid rgba(255,255,255,0.05);">
                        <div style="font-size:11px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px;">Conditions</div>
                        <div style="font-size:13px; color:#cbd5e1; font-weight:600;">${tourney.tempAirDensity}</div>
                        <div style="font-size:12px; color:#94a3b8; margin-top:2px;">Altitude: ${tourney.altitude}</div>
                    </div>
                    <div style="background: rgba(255,255,255,0.03); padding:12px; border-radius:8px; border: 1px solid rgba(255,255,255,0.05);">
                        <div style="font-size:11px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px;">Date</div>
                        <div style="font-size:14px; color:#cbd5e1; font-weight:600;">${tourney.month}</div>
                    </div>
                    <div style="background: rgba(255,255,255,0.03); padding:12px; border-radius:8px; border: 1px solid rgba(255,255,255,0.05);">
                        <div style="font-size:11px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px;">Ball Used</div>
                        <div style="font-size:14px; color:#cbd5e1; font-weight:600;">${tourney.ballType}</div>
                    </div>
                </div>
                
                <!-- Match Data Table -->
                <div style="overflow-x:auto;">
                    <table style="width:100%; border-collapse:collapse; text-align:left; font-size:13px;">
                        <thead>
                            <tr style="border-bottom: 2px solid rgba(255,255,255,0.1);">
                                <th style="padding:10px 12px; color:#64748b; font-weight:600;">Tournament</th>
                                <th style="padding:10px 12px; color:#64748b; font-weight:600;">Year</th>
                                <th style="padding:10px 12px; color:#64748b; font-weight:600;">CPI Avg</th>
                                <th style="padding:10px 12px; color:#64748b; font-weight:600;">Ace Rate</th>
                                <th style="padding:10px 12px; color:#64748b; font-weight:600;">Rally Len</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${tbodyRows}
                        </tbody>
                    </table>
                </div>

            </div>
        `;
    });

    content.innerHTML = html;
}
