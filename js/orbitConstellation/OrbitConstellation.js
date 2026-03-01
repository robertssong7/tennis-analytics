// docs/js/orbitConstellation/OrbitConstellation.js

const HUB_DISTANCE_X = 350; // horizontal spread between hubs
const HUB_START_Y = -150; // start Y relative to center
const LENGTH_Y_SPACING = 50; // vertical drop per pattern length

export class OrbitMap {
    constructor(containerId, globalNodes, options = {}) {
        this.container = document.getElementById(containerId);
        if (!this.container) return;

        // Configuration
        this.isPlayerView = options.mode === 'player';
        this.playerData = options.playerData || null; // Map or Object of pattern_key -> { occurrences, value }
        this.onHover = options.onHover || null;
        this.onClick = options.onClick || null;

        // Map State
        this.nodes = globalNodes;
        this.hubs = {};
        this.transform = { x: 0, y: 0, scale: 1 };
        this.isDragging = false;
        this.lastMouse = { x: 0, y: 0 };
        this.hoveredNode = null;

        this.initCanvas();
        this.computeLayout();
        this.bindEvents();

        // Initial Center
        this.centerCamera();

        // Start Loop
        this.render();
    }

    initCanvas() {
        this.container.innerHTML = '';
        this.container.style.position = 'relative';
        this.container.style.width = '100%';
        this.container.style.height = '100%';
        this.container.style.overflow = 'hidden';
        this.container.style.cursor = 'grab';

        this.canvas = document.createElement('canvas');
        this.canvas.style.display = 'block';
        this.canvas.style.width = '100%';
        this.canvas.style.height = '100%';
        this.container.appendChild(this.canvas);

        this.ctx = this.canvas.getContext('2d', { alpha: false });

        // High DPI Support
        this.resize = () => {
            const rect = this.container.getBoundingClientRect();
            const dpr = window.devicePixelRatio || 1;
            this.canvas.width = rect.width * dpr;
            this.canvas.height = rect.height * dpr;
            this.ctx.scale(dpr, dpr);
            this.width = rect.width;
            this.height = rect.height;
            this.render();
        };

        window.addEventListener('resize', this.resize);
        this.resize();
    }

    computeLayout() {
        // Place Hubs relative to logical center (0,0) horizontally
        this.hubs = {
            'WIDE': { x: -HUB_DISTANCE_X, y: HUB_START_Y },
            'BODY': { x: 0, y: HUB_START_Y },
            'T': { x: HUB_DISTANCE_X, y: HUB_START_Y },
            'ANY': null // Exclude ANY
        };

        // Assign physical coordinates to nodes
        for (const node of this.nodes) {
            const hub = this.hubs[node.serve_dir];
            // Hide if invalid hub
            if (!hub) {
                node.hidden = true;
                continue;
            } else {
                node.hidden = false;
            }

            const depth = Math.max(1, node.length_bucket);
            // Spread nodes horizontally so they don't strictly overlap
            const spreadWidth = 200 + (depth * 25);

            // Map the deterministic hash angle to an X offset
            const horizontalOffset = (node.base_angle / (Math.PI * 2) - 0.5) * spreadWidth;

            // Base coords: waterfall down
            let nx = hub.x + horizontalOffset;
            let ny = hub.y + (depth * LENGTH_Y_SPACING);

            // Very tiny jitter just to prevent perfect stacking collisions of identical matches
            const jitterScale = 4;
            nx += (Math.random() - 0.5) * jitterScale;
            ny += (Math.random() - 0.5) * jitterScale;

            node.renderX = nx;
            node.renderY = ny;

            // Calculate Base Size (tour usage)
            node.baseRadius = Math.max(3, Math.min(12, Math.sqrt(node.players_using) * 1.5));
            node.hitRadius = Math.max(node.baseRadius, 14); // Minimum hit target 14px

            // Default Visuals (Global View)
            node.drawColor = this.getColorForValue(node.tour_value);
            node.drawOpacity = Math.max(0.3, Math.min(1, Math.log10(node.total_occurrences) / 3));
            node.drawBorder = 'rgba(255,255,255,0.1)';

            // Override Visuals for Player View
            if (this.isPlayerView) {
                const pData = this.playerData ? this.playerData[node.id] : null;
                if (!pData || pData.total === 0) {
                    node.drawOpacity = 0.05; // Fade out unused networks
                    node.drawColor = '#475569';
                    node.drawBorder = 'transparent';
                } else {
                    node.drawColor = this.getColorForValue(pData.value);
                    node.drawOpacity = 1;
                    node.drawBorder = '#fff';
                    // Slightly enlarge nodes the player actively uses
                    node.baseRadius *= 1.2;
                }
            }
        }
    }

    // Helper: Blue (Win > Loss avg) -> Gray (Zero) -> Red/Orange (Loss > Win avg)
    getColorForValue(val) {
        if (!val || Math.abs(val) < 0.01) return '#94a3b8'; // baseline gray
        if (val >= 0.06) return '#38bdf8'; // Strong Blue
        if (val > 0) return '#7dd3fc'; // Light Blue
        if (val <= -0.06) return '#f43f5e'; // Strong Red
        return '#fb7185'; // Light Red
    }

    centerCamera() {
        if (!this.width) return;
        this.transform.scale = 0.8; // fit to view
        this.transform.x = this.width / 2;
        this.transform.y = this.height / 2;
    }

    bindEvents() {
        const c = this.canvas;

        c.addEventListener('mousedown', (e) => {
            this.isDragging = true;
            this.lastMouse = { x: e.clientX, y: e.clientY };
            this.container.style.cursor = 'grabbing';
        });

        window.addEventListener('mouseup', () => {
            if (this.isDragging) {
                this.isDragging = false;
                this.container.style.cursor = 'grab';
                // Fire click if no movement
            }
        });

        c.addEventListener('mousemove', (e) => {
            if (this.isDragging) {
                const dx = e.clientX - this.lastMouse.x;
                const dy = e.clientY - this.lastMouse.y;
                this.transform.x += dx;
                this.transform.y += dy;
                this.lastMouse = { x: e.clientX, y: e.clientY };
                this.render();
            } else {
                // Hit Testing
                this.checkHover(e.offsetX, e.offsetY);
            }
        });

        c.addEventListener('wheel', (e) => {
            e.preventDefault();
            const zoomSensitivity = 0.001;
            const delta = -e.deltaY * zoomSensitivity;

            // Scale around mouse cursor
            const mouseX = e.offsetX;
            const mouseY = e.offsetY;

            // Convert mouse to world space
            const worldX = (mouseX - this.transform.x) / this.transform.scale;
            const worldY = (mouseY - this.transform.y) / this.transform.scale;

            let newScale = this.transform.scale * Math.exp(delta);
            newScale = Math.max(0.1, Math.min(newScale, 5)); // Limit zoom

            this.transform.scale = newScale;
            this.transform.x = mouseX - worldX * newScale;
            this.transform.y = mouseY - worldY * newScale;

            this.render();
        }, { passive: false });

        c.addEventListener('click', (e) => {
            if (this.hoveredNode && this.onClick) {
                this.onClick(this.hoveredNode, e);
            }
        });

        c.addEventListener('dblclick', () => {
            this.centerCamera();
            this.render();
        });
    }

    checkHover(mouseX, mouseY) {
        // Convert screen to world space
        const worldX = (mouseX - this.transform.x) / this.transform.scale;
        const worldY = (mouseY - this.transform.y) / this.transform.scale;

        let found = null;
        let minDist = Infinity;

        // Optimized by only checking valid distance
        for (const node of this.nodes) {
            if (node.hidden) continue;

            const dx = node.renderX - worldX;
            const dy = node.renderY - worldY;
            const distSq = dx * dx + dy * dy;

            // Check against hit targets (adjusted by zoom slightly or fixed world size)
            // Divide hitRadius by scale so the hit target size feels consistent on screen
            const hitRadiusWorld = node.hitRadius / this.transform.scale;

            if (distSq <= hitRadiusWorld * hitRadiusWorld) {
                if (distSq < minDist) {
                    minDist = distSq;
                    found = node;
                }
            }
        }

        if (this.hoveredNode !== found) {
            this.hoveredNode = found;
            this.container.style.cursor = found ? 'pointer' : (this.isDragging ? 'grabbing' : 'grab');
            if (this.onHover) this.onHover(found, { x: mouseX, y: mouseY });
            this.render();
        } else if (found && this.onHover) {
            // Update tooltip position while moving mouse inside node
            this.onHover(found, { x: mouseX, y: mouseY });
        }
    }

    updatePlayerOverlay(playerData) {
        this.isPlayerView = true;
        this.playerData = playerData;
        this.computeLayout();
        this.render();
    }

    render() {
        if (!this.ctx) return;

        // Background
        this.ctx.fillStyle = '#0B1120'; // Primary dark navy
        this.ctx.fillRect(0, 0, this.width, this.height);

        this.ctx.save();
        this.ctx.translate(this.transform.x, this.transform.y);
        this.ctx.scale(this.transform.scale, this.transform.scale);

        // Draw Hub Anchors
        this.ctx.lineWidth = 1;

        for (const [key, hub] of Object.entries(this.hubs)) {
            if (!hub) continue;

            // Draw Hub Label/Center point
            this.ctx.fillStyle = 'rgba(255, 255, 255, 1)';
            this.ctx.beginPath();
            this.ctx.arc(hub.x, hub.y, 6, 0, Math.PI * 2);
            this.ctx.fill();

            this.ctx.fillStyle = '#f8fafc';
            this.ctx.font = 'bold 14px Inter';
            this.ctx.textAlign = 'center';
            this.ctx.textBaseline = 'bottom';
            this.ctx.fillText(key + ' SERVE', hub.x, hub.y - 12);
        }

        // Draw Nodes
        // Sort by opacity/importance so bright nodes render on top of faded ones
        const renderQueue = [...this.nodes].filter(n => !n.hidden).sort((a, b) => a.drawOpacity - b.drawOpacity);

        for (const node of renderQueue) {
            this.ctx.globalAlpha = node.drawOpacity;

            // Enlarge slightly if hovered
            const r = (this.hoveredNode === node) ? node.baseRadius * 1.5 : node.baseRadius;

            this.ctx.fillStyle = node.drawColor;
            this.ctx.beginPath();
            this.ctx.arc(node.renderX, node.renderY, r, 0, Math.PI * 2);
            this.ctx.fill();

            if (node.drawBorder !== 'transparent') {
                this.ctx.strokeStyle = node.drawBorder;
                this.ctx.lineWidth = (this.hoveredNode === node) ? 2 : 1;
                this.ctx.stroke();
            }

            // If hovered, draw a subtle glow or indicator
            if (this.hoveredNode === node) {
                this.ctx.strokeStyle = 'rgba(255,255,255, 0.5)';
                this.ctx.lineWidth = 1;
                this.ctx.beginPath();
                this.ctx.arc(node.renderX, node.renderY, r + 4, 0, Math.PI * 2);
                this.ctx.stroke();
            }
        }

        this.ctx.restore();
    }
}
