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

    const { fullName, lastName, countryCode, imageUrl, elo, archetype } = playerProfile;

    const accentColor = side === 'left' ? '#38bdf8' : '#f43f5e';

    container.innerHTML = `
        <div class="compare-card-filled" style="position:relative; display:flex; flex-direction:column; background:rgba(30, 41, 59, 0.5); border-radius:8px; padding:16px; border-top: 4px solid ${accentColor}; min-height:180px;">
            <button class="clear-btn" style="position:absolute; top:8px; right:8px; background:transparent; border:none; color:#f8fafc; cursor:pointer; font-size:16px; opacity:0.5;">&times;</button>
            
            <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                
                <div class="player-photo" style="width:64px; height:64px; border-radius:50%; background:#0f172a; overflow:hidden; border:2px solid rgba(255,255,255,0.1); display:flex; align-items:center; justify-content:center;">
                    <img src="${imageUrl}" style="width:100%; height:100%; object-fit:cover;" onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'64\\' height=\\'64\\'><rect width=\\'64\\' height=\\'64\\' fill=\\'%23334155\\'/><text x=\\'50%\\' y=\\'50%\\' dominant-baseline=\\'middle\\' text-anchor=\\'middle\\' font-size=\\'24\\' fill=\\'%2394a3b8\\'>👤</text></svg>'" />
                </div>
                
                <div class="elo-block" style="text-align:right;">
                    <div style="font-size:32px; font-weight:900; color:#f8fafc; line-height:1; font-family:'JetBrains Mono', monospace;">${elo}</div>
                    <div style="font-size:10px; color:#94a3b8; text-transform:uppercase; letter-spacing:1px; margin-top:4px;">Tour ELO</div>
                </div>

            </div>

            <div style="margin-top:16px;">
                <div style="display:flex; align-items:center; gap:8px;">
                    <span style="font-size:16px; line-height:1;">${getFlagEmoji(countryCode)}</span>
                    <h3 style="margin:0; font-size:18px; color:#f8fafc; font-weight:700;">${lastName}</h3>
                </div>
                <div style="color:#94a3b8; font-size:12px; margin-top:2px;">${fullName}</div>
            </div>

            <div style="margin-top:auto; padding-top:16px;">
                <span style="display:inline-block; padding:4px 8px; background:rgba(255,255,255,0.05); color:#cbd5e1; border-radius:4px; font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px;">${archetype}</span>
            </div>
        </div>
    `;

    container.querySelector('.clear-btn').addEventListener('click', () => {
        if (onClear) onClear(side);
    });
}

function getFlagEmoji(countryCode) {
    if (!countryCode) return '🏳️';
    const codePoints = countryCode
        .toUpperCase()
        .split('')
        .map(char => 127397 + char.charCodeAt(0));
    return String.fromCodePoint(...codePoints);
}
