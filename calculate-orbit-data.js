const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, 'docs', 'data');
const PLAYERS_FILE = path.join(DATA_DIR, 'players.json');

function safeReadJson(filePath) {
    try {
        if (fs.existsSync(filePath)) {
            return JSON.parse(fs.readFileSync(filePath, 'utf8'));
        }
    } catch (e) { }
    return null;
}

// Generate simple hash for deterministic angle spreading
function getHashAngle(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
        hash = ((hash << 5) - hash) + str.charCodeAt(i);
        hash |= 0;
    }
    return (Math.abs(hash) % 1000) / 1000 * Math.PI * 2;
}

function processPlayer(playerName, globalData) {
    const playerDir = path.join(DATA_DIR, 'players', playerName);
    const servePlusOne = safeReadJson(path.join(playerDir, 'serve-plus-one.json')) || [];
    const patternInference = safeReadJson(path.join(playerDir, 'pattern-inference.json')) || { winning: [], losing: [] };

    const addPattern = (sequenceArr, total, value) => {
        if (!sequenceArr || sequenceArr.length === 0 || !total) return;

        const serveHubs = ['T', 'BODY', 'WIDE'];
        let serveDir = 'ANY';
        let length = sequenceArr.length;

        // Check if first element is a serve direction
        if (serveHubs.includes(sequenceArr[0])) {
            serveDir = sequenceArr[0];
        }

        const pattern_key = sequenceArr.map(s => s.replace(/_/g, ' ')).join(' → ');

        if (!globalData.has(pattern_key)) {
            globalData.set(pattern_key, {
                pattern_key,
                serve_dir: serveDir,
                length_bucket: length,
                total_occurrences: 0,
                players: {},
                sum_weighed_value: 0,
                sum_weight: 0
            });
        }

        const node = globalData.get(pattern_key);
        node.total_occurrences += total;

        // Accumulate player usage
        if (!node.players[playerName]) {
            node.players[playerName] = { total: 0, value: value || 0 };
        }
        node.players[playerName].total += total;
        node.players[playerName].value = value || 0;

        // For global tour_value, do weighted average
        if (value !== undefined && value !== null) {
            node.sum_weighed_value += (value * total);
            node.sum_weight += total;
        }
    };

    // 1. Parse Serve+1
    for (const item of servePlusOne) {
        addPattern([item.serveDir, item.responseType], item.total, item.adjustedEffectiveness);
    }

    // 2. Parse Inference (winning/losing)
    const processInference = (list) => {
        for (const item of list) {
            addPattern(item.sequence, item.total, item.uplift); // Using uplift as value proxy here
        }
    };
    processInference(patternInference.winning);
    processInference(patternInference.losing);
}

function buildGraph() {
    const players = safeReadJson(PLAYERS_FILE) || [];
    console.log(`Building global orbit graph for ${players.length} players...`);

    const globalData = new Map();
    for (const player of players) {
        processPlayer(player, globalData);
    }

    // Flatten and finalize nodes
    const MIN_OCCURRENCES_PER_PLAYER = 10;
    const finalNodes = [];

    for (const [key, node] of globalData.entries()) {
        let players_using = 0;
        for (const p of Object.values(node.players)) {
            if (p.total >= MIN_OCCURRENCES_PER_PLAYER) {
                players_using++;
            }
        }

        // Only keep nodes that have any significant usage
        if (players_using > 0 || node.total_occurrences >= 20) {
            const tour_value = node.sum_weight > 0 ? (node.sum_weighed_value / node.sum_weight) : 0;

            finalNodes.push({
                id: key, // using pattern_key as id
                pattern_key: node.pattern_key,
                serve_dir: node.serve_dir,
                length_bucket: node.length_bucket,
                total_occurrences: node.total_occurrences,
                players_using: players_using,
                tour_value: tour_value,
                // Assign deterministic base coordinates around 0,0 (frontend offsets by hub)
                base_angle: getHashAngle(node.pattern_key)
            });
        }
    }

    // Simple Collision Pass (Radius Spreading)
    // Group by hub and length
    const buckets = {};
    for (const node of finalNodes) {
        const hash = `${node.serve_dir}-${node.length_bucket}`;
        if (!buckets[hash]) buckets[hash] = [];
        buckets[hash].push(node);
    }

    // Just spread them purely by deterministic angle for now, the frontend canvas 
    // engine will dynamically draw concentric rings and offset overlap if needed.
    // The base_angle serves as the anchor point.

    const outPath = path.join(DATA_DIR, 'globalPatternGraph.json');
    fs.writeFileSync(outPath, JSON.stringify({ nodes: finalNodes }, null, 2));

    console.log(`Generated orbit graph with ${finalNodes.length} universal pattern nodes.`);
}

buildGraph();
