const fs = require('fs');
const path = require('path');
const http = require('http');

const PORT = 3001;
const BASE_URL = `http://localhost:${PORT}/api`;
const DATA_DIR = path.join(__dirname, 'docs', 'data');

// Ensure directory exists
function ensureDir(dir) {
    if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
    }
}

function fetchJson(url) {
    return new Promise((resolve, reject) => {
        http.get(url, (res) => {
            if (res.statusCode !== 200) {
                reject(new Error(`Failed to fetch ${url}, status: ${res.statusCode}`));
                return;
            }
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => resolve(JSON.parse(data)));
        }).on('error', reject);
    });
}

async function exportStaticData() {
    console.log('Starting static data export. Please ensure the server is running on port ' + PORT);
    ensureDir(DATA_DIR);

    try {
        // Fetch all players
        console.log(`Fetching all players...`);
        const players = await fetchJson(`${BASE_URL}/players?all=true`);
        fs.writeFileSync(path.join(DATA_DIR, 'players.json'), JSON.stringify(players, null, 2));
        console.log(`Saved players.json (${players.length} players)`);

        const endpoints = [
            'coverage',
            'patterns',
            'serve',
            'serve-plus-one',
            'compare',
            'direction-patterns',
            'insights',
            'pattern-inference'
        ];

        for (const player of players) {
            console.log(`\nExporting data for ${player}...`);
            const playerDir = path.join(DATA_DIR, 'players', player);
            ensureDir(playerDir);

            for (const endpoint of endpoints) {
                try {
                    const data = await fetchJson(`${BASE_URL}/player/${encodeURIComponent(player)}/${endpoint}`);
                    fs.writeFileSync(path.join(playerDir, `${endpoint}.json`), JSON.stringify(data, null, 2));
                    console.log(`  - Saved ${endpoint}.json`);
                } catch (err) {
                    console.error(`  - Error fetching ${endpoint} for ${player}:`, err.message);
                }
            }
        }

        console.log('\nStatic export complete!');
        process.exit(0);

    } catch (err) {
        console.error('Export failed:', err);
        process.exit(1);
    }
}

exportStaticData();
