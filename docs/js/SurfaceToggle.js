/**
 * SurfaceToggle — Shared Apple-style segmented control for surface filtering.
 * Reusable across player dashboard and compare modal.
 *
 * Usage:
 *   import { createSurfaceToggle } from './SurfaceToggle.js';
 *   const toggle = createSurfaceToggle(containerId, onSurfaceChange);
 *   toggle.getValue(); // 'all' | 'Hard' | 'Clay' | 'Grass'
 */

const SURFACES = [
    { key: 'all', label: 'All', color: '#e2e8f0', textActive: '#1e293b' },
    { key: 'Hard', label: 'Hard', color: '#0072BC', textActive: '#ffffff' },  // US Open blue
    { key: 'Clay', label: 'Clay', color: '#C75B12', textActive: '#ffffff' },  // French Open terracotta
    { key: 'Grass', label: 'Grass', color: '#006633', textActive: '#ffffff' },  // Wimbledon green
];

const TOGGLE_CSS_ID = 'surface-toggle-styles';
const TOGGLE_STYLES = `
.surface-toggle {
    display: inline-flex;
    background: rgba(255,255,255,0.06);
    border-radius: 10px;
    padding: 3px;
    gap: 2px;
    border: 1px solid rgba(255,255,255,0.08);
    position: relative;
}
.surface-toggle-btn {
    position: relative;
    z-index: 1;
    padding: 6px 16px;
    border: none;
    border-radius: 8px;
    font-size: 12px;
    font-weight: 600;
    font-family: 'Inter', -apple-system, sans-serif;
    cursor: pointer;
    transition: color 0.25s ease, background 0.25s ease;
    background: transparent;
    color: #94a3b8;
    letter-spacing: 0.3px;
    white-space: nowrap;
}
.surface-toggle-btn:hover:not(.active) {
    color: #cbd5e1;
    background: rgba(255,255,255,0.04);
}
.surface-toggle-btn.active {
    box-shadow: 0 1px 4px rgba(0,0,0,0.25), 0 0 0 1px rgba(255,255,255,0.06);
}
`;

function injectStyles() {
    if (document.getElementById(TOGGLE_CSS_ID)) return;
    const style = document.createElement('style');
    style.id = TOGGLE_CSS_ID;
    style.textContent = TOGGLE_STYLES;
    document.head.appendChild(style);
}

export function createSurfaceToggle(container, onChange) {
    injectStyles();

    const host = typeof container === 'string' ? document.getElementById(container) : container;
    if (!host) return null;

    let current = 'all';

    const wrapper = document.createElement('div');
    wrapper.className = 'surface-toggle';

    SURFACES.forEach(s => {
        const btn = document.createElement('button');
        btn.className = 'surface-toggle-btn';
        btn.dataset.surface = s.key;
        btn.textContent = s.label;
        btn.addEventListener('click', () => {
            if (current === s.key) return;
            current = s.key;
            updateActive();
            if (onChange) onChange(s.key);
        });
        wrapper.appendChild(btn);
    });

    function updateActive() {
        wrapper.querySelectorAll('.surface-toggle-btn').forEach(btn => {
            const surf = SURFACES.find(s => s.key === btn.dataset.surface);
            if (btn.dataset.surface === current) {
                btn.classList.add('active');
                btn.style.background = surf.color;
                btn.style.color = surf.textActive;
            } else {
                btn.classList.remove('active');
                btn.style.background = 'transparent';
                btn.style.color = '#94a3b8';
            }
        });
    }

    updateActive();
    host.innerHTML = '';
    host.appendChild(wrapper);

    return {
        getValue: () => current,
        setValue: (key) => {
            current = key;
            updateActive();
        },
        getElement: () => wrapper,
    };
}
