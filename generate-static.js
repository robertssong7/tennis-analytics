const fs = require('fs');
const path = require('path');
const http = require('http');

const PORT = 3001;
const BASE_URL = `http://localhost:${PORT}/api`;
const DATA_DIR = path.join(__dirname, 'docs', 'data');

const SURFACES = ['all', 'Hard', 'Clay', 'Grass'];

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
            'pattern-inference',
            'serve-detailed'
        ];

        for (const player of players) {
            console.log(`\nExporting data for ${player}...`);
            const playerDir = path.join(DATA_DIR, 'players', player);
            ensureDir(playerDir);

            // Export for each surface
            for (const surface of SURFACES) {
                const surfaceParam = surface === 'all' ? '' : `?surface=${surface}`;
                const surfaceDir = surface === 'all' ? playerDir : path.join(playerDir, 'surface', surface);
                ensureDir(surfaceDir);

                for (const endpoint of endpoints) {
                    try {
                        const sep = surface === 'all' ? '?' : '&';
                        const url = `${BASE_URL}/player/${encodeURIComponent(player)}/${endpoint}${surfaceParam}`;
                        const data = await fetchJson(url);
                        fs.writeFileSync(path.join(surfaceDir, `${endpoint}.json`), JSON.stringify(data, null, 2));
                        if (surface === 'all') {
                            // console.log(`  - Saved ${endpoint}.json`);
                        }
                    } catch (err) {
                        // Some endpoints may return no data for certain surfaces, that's OK
                        if (surface === 'all') {
                            console.error(`  - Error fetching ${endpoint} for ${player}:`, err.message);
                        }
                    }
                }
            }
            console.log(`  ✓ All surfaces exported`);
        }

        // Export H2H data for all player pairs
        console.log('\n--- Generating H2H data ---');
        const h2hDir = path.join(DATA_DIR, 'h2h');
        ensureDir(h2hDir);

        let h2hCount = 0;
        for (let i = 0; i < players.length; i++) {
            for (let j = i + 1; j < players.length; j++) {
                try {
                    const url = `${BASE_URL}/h2h/${encodeURIComponent(players[i])}/${encodeURIComponent(players[j])}`;
                    const data = await fetchJson(url);
                    if (data.totalMatches > 0) {
                        const key = [players[i], players[j]].sort().join('_vs_');
                        fs.writeFileSync(path.join(h2hDir, `${key}.json`), JSON.stringify(data, null, 2));
                        h2hCount++;
                    }
                } catch (err) {
                    // Skip pairs with no data
                }
            }
        }
        console.log(`Saved ${h2hCount} H2H files`);

        console.log('\nStatic export complete!');
        process.exit(0);

    } catch (err) {
        console.error('Export failed:', err);
        process.exit(1);
    }
}

exportStaticData();
