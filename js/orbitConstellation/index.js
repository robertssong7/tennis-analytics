// docs/js/orbitConstellation/index.js
import { fetchGlobalOrbitGraph } from './dataModel.js';
import { OrbitMap } from './OrbitConstellation.js';
import { createHoverTooltip, updateHoverTooltip, createDetailCard, updateDetailCard } from './tooltip.js';

let globalGraphData = null;
let globalMapInstance = null;
let playerMapInstance = null;

let globalHoverEl = null;
let globalDetailEl = null;
let playerHoverEl = null;
let playerDetailEl = null;

export async function initGlobalOrbit(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    globalGraphData = await fetchGlobalOrbitGraph();
    if (!globalGraphData || globalGraphData.length === 0) return;

    // Setup UI Tooltips overlaying the canvas
    if (!globalHoverEl) globalHoverEl = createHoverTooltip(container);
    if (!globalDetailEl) globalDetailEl = createDetailCard(container);

    // Build Map
    globalMapInstance = new OrbitMap(containerId, globalGraphData, {
        mode: 'global',
        onHover: (node, pos) => {
            updateHoverTooltip(globalHoverEl, node, pos);
        },
        onClick: (node) => {
            updateDetailCard(globalDetailEl, node, false, null);
        }
    });

    return globalMapInstance;
}

// Maps node.id (pattern_key) -> { total, value (uplift or adjusted eff) }
export async function renderPlayerOrbit(containerId, playerModelData) {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (!globalGraphData) {
        globalGraphData = await fetchGlobalOrbitGraph();
        if (!globalGraphData || globalGraphData.length === 0) return;
    }

    if (!playerHoverEl) playerHoverEl = createHoverTooltip(container);
    if (!playerDetailEl) playerDetailEl = createDetailCard(container);

    if (!playerMapInstance) {
        playerMapInstance = new OrbitMap(containerId, globalGraphData, {
            mode: 'player',
            playerData: playerModelData,
            onHover: (node, pos) => {
                updateHoverTooltip(playerHoverEl, node, pos);
            },
            onClick: (node) => {
                updateDetailCard(playerDetailEl, node, true, playerModelData);
            }
        });
    } else {
        // Soft refresh layout
        playerMapInstance.updatePlayerOverlay(playerModelData);
        // Force hide open cards if any when switching players
        if (playerDetailEl) playerDetailEl.style.display = 'none';
        if (playerHoverEl) playerHoverEl.style.display = 'none';
    }
}
