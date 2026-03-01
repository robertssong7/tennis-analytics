// docs/js/orbitConstellation/tooltip.js

// Short Hover Tooltip
export function createHoverTooltip(container) {
    const el = document.createElement('div');
    el.style.position = 'absolute';
    el.style.pointerEvents = 'none';
    el.style.background = 'rgba(15, 23, 42, 0.9)';
    el.style.color = '#fff';
    el.style.border = '1px solid #334155';
    el.style.padding = '8px 12px';
    el.style.borderRadius = '6px';
    el.style.fontFamily = 'Inter, sans-serif';
    el.style.fontSize = '12px';
    el.style.zIndex = '100';
    el.style.display = 'none';
    el.style.boxShadow = '0 4px 6px rgba(0,0,0,0.3)';
    el.style.whiteSpace = 'nowrap';

    container.appendChild(el);
    return el;
}

export function updateHoverTooltip(el, node, pos) {
    if (!node) {
        el.style.display = 'none';
        return;
    }
    el.style.display = 'block';

    // Shorten pattern
    let shortKey = node.pattern_key.length > 30 ? node.pattern_key.substring(0, 27) + '...' : node.pattern_key;

    el.innerHTML = `
        <div style="font-weight:600; margin-bottom:4px; max-width: 250px; overflow: hidden; text-overflow: ellipsis;">${shortKey}</div>
        <div style="color:#94a3b8;">
            <span style="color:#38bdf8">👥 ${node.players_using}</span> players • <span>🔄 ${node.total_occurrences}</span> uses
        </div>
    `;

    // Position offset
    el.style.left = (pos.x + 15) + 'px';
    el.style.top = (pos.y + 15) + 'px';
}

// Side Panel / Click Detail Card
export function createDetailCard(container) {
    const el = document.createElement('div');
    el.style.position = 'absolute';
    el.style.top = '16px';
    el.style.right = '16px';
    el.style.width = '300px';
    el.style.background = '#1e293b';
    el.style.border = '1px solid #334155';
    el.style.borderRadius = '8px';
    el.style.padding = '16px';
    el.style.color = '#f8fafc';
    el.style.fontFamily = 'Inter, sans-serif';
    el.style.zIndex = '90';
    el.style.display = 'none';
    el.style.boxShadow = '0 10px 15px -3px rgba(0,0,0,0.3)';

    container.appendChild(el);
    return el;
}

export function updateDetailCard(el, node, isPlayerView, playerData) {
    if (!node) {
        el.style.display = 'none';
        return;
    }
    el.style.display = 'block';

    const pData = (isPlayerView && playerData) ? playerData[node.id] : null;

    let html = `
        <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:12px;">
            <h4 style="margin:0; font-size:14px; color:#38bdf8; font-weight:600;">Pattern Details</h4>
            <button id="close-detail-card" style="background:none; border:none; color:#94a3b8; cursor:pointer; font-size:16px; padding:0; line-height:1;">&times;</button>
        </div>
        <div style="font-family:'JetBrains Mono', monospace; font-size:13px; background:#0f172a; padding:10px; border-radius:6px; margin-bottom:16px; word-break:break-all;">
            ${node.pattern_key}
        </div>
        
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; font-size:13px;">
            <div>
                <div style="color:#94a3b8; margin-bottom:2px; font-size:11px; text-transform:uppercase;">Tour Uses</div>
                <div style="font-weight:600;">${node.total_occurrences.toLocaleString()}</div>
            </div>
            <div>
                <div style="color:#94a3b8; margin-bottom:2px; font-size:11px; text-transform:uppercase;">Adoption</div>
                <div style="font-weight:600;">${node.players_using} players</div>
            </div>
        </div>
    `;

    if (isPlayerView && pData && pData.total > 0) {
        const valStr = (pData.value > 0 ? '+' : '') + (pData.value * 100).toFixed(1) + '%';
        html += `
            <div style="margin-top:16px; padding-top:16px; border-top:1px solid #334155;">
                <h5 style="margin:0 0 8px 0; font-size:12px; color:#cbd5e1;">Selected Player View</h5>
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; font-size:13px;">
                    <div>
                        <div style="color:#94a3b8; margin-bottom:2px; font-size:11px;">USES</div>
                        <div style="font-weight:600; color:#fff;">${pData.total}</div>
                    </div>
                    <div>
                        <div style="color:#94a3b8; margin-bottom:2px; font-size:11px;">VALUE OVER BASELINE</div>
                        <div style="font-weight:600; color:${pData.value > 0 ? '#10b981' : '#f43f5e'};">${valStr}</div>
                    </div>
                </div>
            </div>
        `;
    } else if (isPlayerView) {
        html += `
            <div style="margin-top:16px; padding-top:16px; border-top:1px solid #334155; font-size:12px; color:#64748b; font-style:italic;">
                Player has not used this pattern contextually often enough to rank.
            </div>
        `;
    }

    el.innerHTML = html;

    // Wire close button
    const closeBtn = el.querySelector('#close-detail-card');
    if (closeBtn) {
        closeBtn.onclick = () => { el.style.display = 'none'; };
    }
}
