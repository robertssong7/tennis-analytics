export function renderCompareBars(containerId, playerA, playerB) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.innerHTML = '';

    // We expect both players to have the exact same attributes array definition
    // fallback gracefully if one is missing
    const attributesDef = playerA ? playerA.attributes : (playerB ? playerB.attributes : []);

    if (attributesDef.length === 0) return;

    const wrapper = document.createElement('div');
    wrapper.className = 'compare-bars-wrapper';
    wrapper.style.display = 'flex';
    wrapper.style.flexDirection = 'column';
    wrapper.style.gap = '12px';
    wrapper.style.width = '100%';

    attributesDef.forEach((attr, idx) => {
        const valA = playerA ? playerA.attributes.find(a => a.key === attr.key)?.value : null;
        const valB = playerB ? playerB.attributes.find(a => a.key === attr.key)?.value : null;

        const row = document.createElement('div');
        row.className = 'compare-bar-row';
        row.style.display = 'flex';
        row.style.alignItems = 'center';
        row.style.width = '100%';
        row.style.position = 'relative';

        // Label Center
        const labelDiv = document.createElement('div');
        labelDiv.style.flex = '0 0 120px';
        labelDiv.style.textAlign = 'center';
        labelDiv.style.fontSize = '12px';
        labelDiv.style.fontWeight = '600';
        labelDiv.style.color = '#cbd5e1';
        labelDiv.style.zIndex = '2';

        let labelText = attr.label;
        if (attr.note) {
            labelText += ` <span style="color:#64748b; font-size:10px; font-weight:normal;">(${attr.note})</span>`;
        }
        labelDiv.innerHTML = labelText;

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
        barAFill.style.background = '#38bdf8'; // Blue
        barAFill.style.borderRadius = '4px 0 0 4px';
        barAFill.style.transition = 'width 0.4s easeOut';

        // Render Value Number Float
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
        barBFill.style.background = '#f43f5e'; // Red
        barBFill.style.borderRadius = '0 4px 4px 0';
        barBFill.style.transition = 'width 0.4s easeOut';

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

        // Highlight Winner Logic (Optional Chrome effect)
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
