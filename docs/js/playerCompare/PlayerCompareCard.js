export function renderCompareCard(containerId, playerProfile, side = 'left', onClear) {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (!playerProfile) {
        container.innerHTML = `
            <div class="empty-card" style="display:flex; flex-direction:column; align-items:center; justify-content:center; height:100%; min-height:180px; border: 1px dashed rgba(255,255,255,0.1); border-radius: 8px; color: #64748b; font-size: 14px;">
                <div style="font-size:32px; margin-bottom:8px;">+</div>
                Select Player ${side === 'left' ? 'A' : 'B'}
            </div>
        `;
        return;
    }

    const { fullName, lastName, countryCode, imageUrl, percentiles } = playerProfile;

    // Compute overall rating: average of non-null radar percentiles
    const radarKeys = ['serve', 'ground_consistency', 'aggression_efficiency', 'volley_win', 'break_point_defense', 'aggregate_consistency'];
    const validVals = radarKeys.map(k => percentiles?.[k]).filter(v => v !== null && v !== undefined);
    const overallRating = validVals.length > 0 ? Math.round(validVals.reduce((a, b) => a + b, 0) / validVals.length) : '—';

    const accentColor = side === 'left' ? '#38bdf8' : '#f43f5e';

    container.innerHTML = `
        <div class="compare-card-filled" style="position:relative; display:flex; flex-direction:column; background:rgba(30, 41, 59, 0.4); border-radius:12px; padding:20px; border: 1px solid rgba(255,255,255,0.05); min-height:200px; overflow:hidden;">
            <div style="position:absolute; top:0; left:0; width:4px; height:100%; background:${accentColor};"></div>
            <button class="clear-btn" style="position:absolute; top:8px; right:8px; background:transparent; border:none; color:#94a3b8; cursor:pointer; font-size:20px; transition:color 0.2s;">&times;</button>
            
            <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                
                <div class="player-photo-shield" style="position:relative;">
                    <div style="width:72px; height:72px; border-radius:50%; background:#0f172a; overflow:hidden; border:2px solid ${accentColor}44; display:flex; align-items:center; justify-content:center;">
                        <img src="${imageUrl}" style="width:100%; height:100%; object-fit:cover;" onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'72\\' height=\\'72\\'><rect width=\\'72\\' height=\\'72\\' fill=\\'%231e293b\\'/><text x=\\'50%\\' y=\\'50%\\' dominant-baseline=\\'middle\\' text-anchor=\\'middle\\' font-size=\\'28\\' fill=\\'%23475569\\'>👤</text></svg>'" />
                    </div>
                    <div style="position:absolute; bottom:-5px; right:-5px; background:${accentColor}; color:#0f172a; font-size:10px; font-weight:800; padding:2px 6px; border-radius:10px; border:2px solid #0f172a;">
                        ${countryCode}
                    </div>
                </div>
                
                <div class="rating-block" style="text-align:right;">
                    <div style="background: linear-gradient(135deg, ${accentColor}22 0%, ${accentColor}11 100%); padding: 8px 12px; border-radius: 10px; border: 1px solid ${accentColor}33;">
                        <div style="font-size:38px; font-weight:900; color:#f8fafc; line-height:1; font-family:'Inter', sans-serif;">${overallRating}</div>
                        <div style="font-size:9px; color:${accentColor}; text-transform:uppercase; font-weight:800; letter-spacing:1px; margin-top:2px;">Overall</div>
                    </div>
                </div>

            </div>

            <div style="margin-top:20px;">
                <h3 style="margin:0; font-size:22px; color:#f8fafc; font-weight:800; letter-spacing:-0.5px;">${lastName}</h3>
                <div style="color:#94a3b8; font-size:13px; margin-top:2px;">${fullName}</div>
            </div>
        </div>
    `;

    container.querySelector('.clear-btn').addEventListener('click', () => {
        if (onClear) onClear(side);
    });
}
