import { METRIC_DEFS } from './playerCompareModel.js';

export function renderCompareBars(containerId, playerA, playerB) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.innerHTML = '';

    const attributesDef = playerA ? playerA.attributes : (playerB ? playerB.attributes : []);
    if (attributesDef.length === 0) return;

    // ── Info panel toggle button (top-right) ──
    const headerRow = document.createElement('div');
    headerRow.style.cssText = 'display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;';

    const title = document.createElement('div');
    title.style.cssText = 'font-size:13px; color:#64748b; font-weight:600; text-transform:uppercase; letter-spacing:0.5px;';
    title.textContent = 'Attributes';

    const infoBtn = document.createElement('button');
    infoBtn.className = 'attr-info-btn';
    infoBtn.innerHTML = 'ⓘ';
    infoBtn.title = 'Show all attribute descriptions';
    infoBtn.style.cssText = `
        background: rgba(148,163,184,0.1); border: 1px solid rgba(148,163,184,0.25);
        color: #94a3b8; font-size: 18px; cursor: pointer; width: 32px; height: 32px;
        border-radius: 50%; display: flex; align-items: center; justify-content: center;
        transition: all 0.2s ease; line-height: 1;
    `;
    infoBtn.addEventListener('mouseenter', () => { infoBtn.style.color = '#f8fafc'; infoBtn.style.borderColor = 'rgba(248,250,252,0.4)'; });
    infoBtn.addEventListener('mouseleave', () => { infoBtn.style.color = '#94a3b8'; infoBtn.style.borderColor = 'rgba(148,163,184,0.25)'; });

    headerRow.appendChild(title);
    headerRow.appendChild(infoBtn);
    container.appendChild(headerRow);

    // ── Info panel (hidden by default, floating) ──
    const infoPanel = document.createElement('div');
    infoPanel.className = 'info-overlay-popup';
    infoPanel.innerHTML = `
        <div style="font-size:14px; font-weight:700; color:#f8fafc; margin-bottom:10px;">Attribute Guide</div>
        ${METRIC_DEFS.map(def => `
            <div style="margin-bottom:8px; display:flex; gap:8px; align-items:flex-start;">
                <span style="font-size:12px; font-weight:700; color:#38bdf8; min-width:80px; flex-shrink:0;">${def.label}</span>
                <span style="font-size:12px; color:#94a3b8; line-height:1.4;">${def.description}</span>
            </div>
        `).join('')}
        <div style="margin-top:10px; font-size:11px; color:#475569;">All values are percentiles (1–100) relative to the tour.</div>
    `;

    // Append to body so it escapes the container overflow formatting
    document.body.appendChild(infoPanel);

    infoBtn.addEventListener('mouseenter', (e) => {
        infoBtn.style.color = '#f8fafc';
        infoBtn.style.borderColor = 'rgba(248,250,252,0.4)';

        const rect = infoBtn.getBoundingClientRect();
        infoPanel.style.display = 'block';

        // Position it right below or above the button
        let topPos = rect.bottom + window.scrollY + 10;
        let leftPos = rect.right + window.scrollX - 300; // Align right edge roughly

        // Ensure it doesn't go off screen left
        if (leftPos < 20) leftPos = 20;

        infoPanel.style.top = `${topPos}px`;
        infoPanel.style.left = `${leftPos}px`;
    });

    infoBtn.addEventListener('mouseleave', () => {
        infoBtn.style.color = '#94a3b8';
        infoBtn.style.borderColor = 'rgba(148,163,184,0.25)';
        infoPanel.style.display = 'none';
    });

    // Cleanup when container is rewritten/unmounted
    const observer = new MutationObserver(() => {
        if (!document.body.contains(container)) {
            if (infoPanel.parentNode) infoPanel.parentNode.removeChild(infoPanel);
            observer.disconnect();
        }
    });
    observer.observe(document.body, { childList: true, subtree: true });

    // ── Attribute Bars ──
    const wrapper = document.createElement('div');
    wrapper.className = 'compare-bars-wrapper';
    wrapper.style.display = 'flex';
    wrapper.style.flexDirection = 'column';
    wrapper.style.gap = '12px';
    wrapper.style.width = '100%';

    attributesDef.forEach((attr) => {
        const valA = playerA ? playerA.attributes.find(a => a.key === attr.key)?.value : null;
        const valB = playerB ? playerB.attributes.find(a => a.key === attr.key)?.value : null;

        const row = document.createElement('div');
        row.className = 'compare-bar-row';
        row.style.display = 'flex';
        row.style.alignItems = 'center';
        row.style.width = '100%';
        row.style.position = 'relative';

        // Label Center — hoverable with tooltip
        const labelDiv = document.createElement('div');
        labelDiv.style.flex = '0 0 120px';
        labelDiv.style.textAlign = 'center';
        labelDiv.style.fontSize = '12px';
        labelDiv.style.fontWeight = '600';
        labelDiv.style.color = '#cbd5e1';
        labelDiv.style.zIndex = '2';
        labelDiv.style.cursor = 'help';
        labelDiv.style.position = 'relative';

        // Get description from METRIC_DEFS
        const metricDef = METRIC_DEFS.find(d => d.key === attr.key);
        const desc = metricDef?.description || '';

        let labelText = attr.label;
        if (attr.note) {
            labelText += ` <span style="color:#64748b; font-size:10px; font-weight:normal;">(${attr.note})</span>`;
        }
        labelDiv.innerHTML = labelText;

        // Custom tooltip via data attribute + CSS
        labelDiv.setAttribute('data-tooltip', desc);
        labelDiv.classList.add('attr-label-tooltip');

        // Player A Side (Left)
        const leftSide = document.createElement('div');
        leftSide.style.flex = '1';
        leftSide.style.display = 'flex';
        leftSide.style.flexDirection = 'row-reverse';
        leftSide.style.alignItems = 'center';
        leftSide.style.justifyContent = 'flex-start';
        leftSide.style.paddingRight = '10px';

        const barAWrapper = document.createElement('div');
        barAWrapper.style.width = '100%';
        barAWrapper.style.height = '14px';
        barAWrapper.style.background = 'rgba(255,255,255,0.05)';
        barAWrapper.style.borderRadius = '4px 0 0 4px';
        barAWrapper.style.position = 'relative';

        const valAObj = valA !== null && valA !== undefined ? valA : 0;
        const barAFill = document.createElement('div');
        barAFill.style.position = 'absolute';
        barAFill.style.right = '0';
        barAFill.style.top = '0';
        barAFill.style.height = '100%';
        barAFill.style.width = `${valAObj}%`;
        barAFill.style.background = '#38bdf8';
        barAFill.style.borderRadius = '4px 0 0 4px';
        barAFill.style.transition = 'width 0.4s ease-out';

        const numA = document.createElement('span');
        numA.style.marginLeft = '8px';
        numA.style.fontSize = '13px';
        numA.style.fontWeight = '700';
        numA.style.color = valA === null ? '#64748b' : '#f8fafc';
        numA.innerText = valA === null ? '—' : valA;

        if (valA !== null) barAWrapper.appendChild(barAFill);
        leftSide.appendChild(barAWrapper);
        leftSide.appendChild(numA);

        // Player B Side (Right)
        const rightSide = document.createElement('div');
        rightSide.style.flex = '1';
        rightSide.style.display = 'flex';
        rightSide.style.flexDirection = 'row';
        rightSide.style.alignItems = 'center';
        rightSide.style.justifyContent = 'flex-start';
        rightSide.style.paddingLeft = '10px';

        const barBWrapper = document.createElement('div');
        barBWrapper.style.width = '100%';
        barBWrapper.style.height = '14px';
        barBWrapper.style.background = 'rgba(255,255,255,0.05)';
        barBWrapper.style.borderRadius = '0 4px 4px 0';
        barBWrapper.style.position = 'relative';

        const valBObj = valB !== null && valB !== undefined ? valB : 0;
        const barBFill = document.createElement('div');
        barBFill.style.position = 'absolute';
        barBFill.style.left = '0';
        barBFill.style.top = '0';
        barBFill.style.height = '100%';
        barBFill.style.width = `${valBObj}%`;
        barBFill.style.background = '#f43f5e';
        barBFill.style.borderRadius = '0 4px 4px 0';
        barBFill.style.transition = 'width 0.4s ease-out';

        const numB = document.createElement('span');
        numB.style.marginRight = '8px';
        numB.style.fontSize = '13px';
        numB.style.fontWeight = '700';
        numB.style.color = valB === null ? '#64748b' : '#f8fafc';
        numB.innerText = valB === null ? '—' : valB;

        if (valB !== null) barBWrapper.appendChild(barBFill);
        rightSide.appendChild(barBWrapper);
        rightSide.appendChild(numB);

        // Construct Row
        row.appendChild(leftSide);
        row.appendChild(labelDiv);
        row.appendChild(rightSide);

        wrapper.appendChild(row);

        // Highlight Winner
        if (valA !== null && valB !== null && valA !== valB) {
            const isAWinner = valA > valB;
            if (isAWinner) {
                numA.style.color = '#38bdf8';
                barBFill.style.opacity = '0.6';
            } else {
                numB.style.color = '#f43f5e';
                barAFill.style.opacity = '0.6';
            }
        }
    });

    container.appendChild(wrapper);
}
