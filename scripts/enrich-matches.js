/**
 * Enrich matches with opponent rank tier from JeffSackmann ATP rankings.
 * Also derives opponent_hand from existing p1_hand/p2_hand columns.
 * 
 * Run once: node scripts/enrich-matches.js
 * Re-run with WHERE filter for incremental updates.
 */
require('dotenv').config();
const { Pool } = require('pg');
const https = require('https');
const { normalizePlayerName } = require('./utils/name-utils');

const pool = new Pool({
    user: process.env.DB_USER,
    password: process.env.DB_PASSWORD,
    host: process.env.DB_HOST,
    port: process.env.DB_PORT,
    database: process.env.DB_NAME,
});

const RANKINGS_URL = 'https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_rankings_current.csv';
const PLAYERS_URL = 'https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_players.csv';

function fetchCSV(url) {
    return new Promise((resolve, reject) => {
        https.get(url, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => resolve(data));
            res.on('error', reject);
        }).on('error', reject);
    });
}

function parseCSVRows(csvText) {
    const lines = csvText.split('\n');
    const headers = lines[0].split(',').map(h => h.trim());
    const rows = [];
    for (let i = 1; i < lines.length; i++) {
        if (!lines[i].trim()) continue;
        const vals = lines[i].split(',');
        const row = {};
        headers.forEach((h, idx) => { row[h] = (vals[idx] || '').trim(); });
        rows.push(row);
    }
    return rows;
}

function rankToTier(rank) {
    const r = parseInt(rank);
    if (!r || isNaN(r)) return null;
    if (r <= 10) return 'top10';
    if (r <= 50) return '11-50';
    if (r <= 100) return '51-100';
    return '100+';
}

async function run() {
    console.log('=== Match Enrichment ===');

    // 1. Add column
    console.log('Adding opponent_rank_tier column...');
    await pool.query('ALTER TABLE match ADD COLUMN IF NOT EXISTS opponent_rank_tier VARCHAR(10);');

    // 2. Load current rankings
    console.log('Fetching ATP rankings...');
    const rankCSV = await fetchCSV(RANKINGS_URL);
    const rankRows = parseCSVRows(rankCSV);

    // Build ranking lookup: name → rank
    // Rankings CSV has: ranking_date, rank, player (ID)
    // We also need players CSV to map ID → name
    console.log('Fetching ATP players...');
    const playersCSV = await fetchCSV(PLAYERS_URL);
    const playerRows = parseCSVRows(playersCSV);

    // player_id → { name_first, name_last }
    const playerIdToName = {};
    for (const p of playerRows) {
        const id = p.player_id;
        const name = `${p.name_first || ''} ${p.name_last || ''}`.trim();
        if (id && name) {
            playerIdToName[id] = normalizePlayerName(name);
        }
    }

    // Get latest ranking per player
    const latestRanks = {};
    for (const r of rankRows) {
        const playerId = r.player;
        const name = playerIdToName[playerId];
        if (name) {
            latestRanks[name] = parseInt(r.rank) || 9999;
        }
    }
    console.log(`Loaded rankings for ${Object.keys(latestRanks).length} players.`);

    // 3. Load matches needing enrichment
    const matches = await pool.query(`
        SELECT id, first_player_name, second_player_name
        FROM match
        WHERE opponent_rank_tier IS NULL
    `);

    console.log(`Processing ${matches.rows.length} matches...`);
    let updated = 0;

    for (const m of matches.rows) {
        // For each match, we assign rank tiers from BOTH players' perspectives
        // The opponent_rank_tier column stores the tier of the "opponent" — 
        // but since both players appear in the match, we classify based on
        // the higher-ranked player's opponent. For simplicity, we store
        // the rank tier of player 2 (as opponent of player 1).
        const p2Name = normalizePlayerName(m.second_player_name);
        const p2Rank = latestRanks[p2Name];

        if (p2Rank !== undefined) {
            const tier = rankToTier(p2Rank);
            if (tier) {
                await pool.query(
                    'UPDATE match SET opponent_rank_tier = $1 WHERE id = $2',
                    [tier, m.id]
                );
                updated++;
            }
        }
    }

    console.log(`Updated ${updated} matches with opponent_rank_tier.`);

    // Verify
    const stats = await pool.query(`
        SELECT COALESCE(opponent_rank_tier, 'Unknown') AS tier, COUNT(*) AS cnt
        FROM match
        GROUP BY opponent_rank_tier
        ORDER BY cnt DESC
    `);
    console.log('\nRank tier distribution:');
    for (const r of stats.rows) {
        console.log(`  ${r.tier}: ${r.cnt}`);
    }

    await pool.end();
    console.log('Done!');
}

run().catch(err => {
    console.error('Enrichment failed:', err);
    process.exit(1);
});
