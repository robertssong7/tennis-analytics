/**
 * Live Scores Scraper — scrapes live-tennis.eu for ATP live scores.
 * Writes output to docs/data/live-scores.json.
 * 
 * Run: node scripts/scrape-live-scores.js
 * 
 * Interval: 90s when live matches detected, 15min otherwise.
 */

const https = require('https');
const fs = require('fs');
const path = require('path');

let cheerio;
try {
    cheerio = require('cheerio');
} catch {
    console.error('cheerio not installed. Run: npm install cheerio');
    process.exit(1);
}

const LIVE_URL = 'https://live-tennis.eu/en/atp-live-scores';
const OUTPUT_PATH = path.join(__dirname, '..', 'docs', 'data', 'live-scores.json');
const LIVE_INTERVAL = 90_000;     // 90 seconds
const IDLE_INTERVAL = 15 * 60_000; // 15 minutes

// Tournament tier priority
const TIER_PRIORITY = {
    'grand slam': 0,
    'atp masters 1000': 1,
    'atp 500': 2,
    'atp 250': 3,
};

function fetchHTML(url) {
    return new Promise((resolve, reject) => {
        https.get(url, { headers: { 'User-Agent': 'Mozilla/5.0' } }, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => resolve(data));
            res.on('error', reject);
        }).on('error', reject);
    });
}

function parseScores(html) {
    const $ = cheerio.load(html);
    const matches = [];
    let currentTournament = '';
    let currentSurface = '';

    // live-tennis.eu structures matches in tables
    // Each tournament block typically has a header with tournament name
    // and match rows below it
    $('table.livescores_table tr, .draw-header, .league-header, h2, h3').each((_, el) => {
        const $el = $(el);
        const text = $el.text().trim();

        // Detect tournament headers
        if ($el.hasClass('draw-header') || $el.hasClass('league-header') ||
            el.tagName === 'h2' || el.tagName === 'h3') {
            if (text) currentTournament = text;
            return;
        }

        // Try to parse match rows
        const cells = $el.find('td');
        if (cells.length < 3) return;

        const player1 = $(cells[0]).text().trim();
        const player2 = $(cells[1]).text().trim();

        if (!player1 || !player2) return;

        // Extract set scores from remaining cells
        const setScores = [];
        let currentGameScore = '';
        let status = 'scheduled';

        for (let i = 2; i < cells.length; i++) {
            const cellText = $(cells[i]).text().trim();
            if (!cellText) continue;

            // Game score typically has format like "30-15"
            if (cellText.includes('-') && cellText.length <= 5) {
                const parts = cellText.split('-');
                const p1Score = parseInt(parts[0]);
                const p2Score = parseInt(parts[1]);
                if (!isNaN(p1Score) && !isNaN(p2Score) && p1Score <= 7 && p2Score <= 7) {
                    setScores.push({ p1: p1Score, p2: p2Score });
                    status = 'completed';
                } else if (cellText.match(/^\d+-\d+$/) || cellText.match(/^(0|15|30|40|AD)-(0|15|30|40|AD)$/i)) {
                    currentGameScore = cellText;
                    status = 'live';
                }
            }
        }

        // Check for live indicators
        const rowHTML = $el.html() || '';
        if (rowHTML.includes('live') || rowHTML.includes('LIVE') || rowHTML.includes('inprogress')) {
            status = 'live';
        }

        if (player1 && player2 && setScores.length > 0) {
            matches.push({
                player1Name: player1,
                player2Name: player2,
                setScores,
                currentGameScore,
                status,
                tournamentName: currentTournament,
                round: extractRound($el, $),
                surface: currentSurface || null,
            });
        }
    });

    return matches;
}

function extractRound($el, $) {
    // Try to find round info from parent or sibling elements
    const text = $el.closest('.round, [class*=round]').text().trim();
    const roundMap = {
        'final': 'F', 'semi': 'SF', 'quarter': 'QF',
        'r16': 'R16', 'r32': 'R32', 'r64': 'R64', 'r128': 'R128',
    };
    const lower = text.toLowerCase();
    for (const [key, val] of Object.entries(roundMap)) {
        if (lower.includes(key)) return val;
    }
    return null;
}

function selectFeaturedTournament(matches) {
    // Group by tournament
    const tournamentStats = {};
    for (const m of matches) {
        const name = m.tournamentName || 'Unknown';
        if (!tournamentStats[name]) {
            tournamentStats[name] = { name, liveCount: 0, surface: m.surface, round: m.round };
        }
        if (m.status === 'live') tournamentStats[name].liveCount++;
    }

    // Sort by tier priority, then by live match count
    const tournaments = Object.values(tournamentStats);
    const lower = (n) => (n || '').toLowerCase();

    tournaments.sort((a, b) => {
        // Grand Slams first
        const aIsGS = lower(a.name).includes('grand slam') ||
            ['australian open', 'roland garros', 'wimbledon', 'us open'].some(gs => lower(a.name).includes(gs));
        const bIsGS = lower(b.name).includes('grand slam') ||
            ['australian open', 'roland garros', 'wimbledon', 'us open'].some(gs => lower(b.name).includes(gs));
        if (aIsGS && !bIsGS) return -1;
        if (bIsGS && !aIsGS) return 1;

        // Then by live count
        return b.liveCount - a.liveCount;
    });

    return tournaments[0] || null;
}

async function scrape() {
    try {
        console.log(`[${new Date().toISOString()}] Scraping live scores...`);
        const html = await fetchHTML(LIVE_URL);
        const matches = parseScores(html);
        const featured = selectFeaturedTournament(matches);

        const output = {
            fetchedAt: new Date().toISOString(),
            featuredTournament: featured ? {
                name: featured.name,
                surface: featured.surface,
                round: featured.round,
            } : null,
            matches,
        };

        // Ensure output directory exists
        const dir = path.dirname(OUTPUT_PATH);
        if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

        fs.writeFileSync(OUTPUT_PATH, JSON.stringify(output, null, 2));
        console.log(`  Written ${matches.length} matches to live-scores.json`);

        return matches.some(m => m.status === 'live');
    } catch (err) {
        console.error('Scrape error:', err.message);
        return false;
    }
}

// ─── Run Loop ────────────────────────────────────────────────────
let currentInterval = IDLE_INTERVAL;
let timer = null;

async function loop() {
    const hasLive = await scrape();
    const newInterval = hasLive ? LIVE_INTERVAL : IDLE_INTERVAL;

    if (newInterval !== currentInterval) {
        console.log(`  Switching to ${hasLive ? 'live' : 'idle'} polling (${newInterval / 1000}s)`);
        currentInterval = newInterval;
    }

    timer = setTimeout(loop, currentInterval);
}

// Initial scrape
loop();

// Graceful shutdown
process.on('SIGINT', () => {
    clearTimeout(timer);
    console.log('\nScraper stopped.');
    process.exit(0);
});
