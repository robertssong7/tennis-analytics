/**
 * Database setup: adds surface/metadata columns to the match table
 * by joining with Jeff Sackmann's charting-m-matches.csv.
 * 
 * Run once: node db-setup.js
 */

const { Pool } = require('pg');
const https = require('https');
import dotenv from "dotenv";

dotenv.config();

const pool = new Pool({
    user: 'postgres',
    password: process.env.DB_PASSWORD,
    host: process.env.DB_DOMAIN,
    port: process.env.DB_PORT,
    database: process.env.DB_NAME,
});

const CSV_URL = 'https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/refs/heads/master/charting-m-matches.csv';

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

function parseCSV(csvText) {
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

async function run() {
    console.log('Ensuring columns exist...');
    await pool.query(`
    ALTER TABLE match ADD COLUMN IF NOT EXISTS surface VARCHAR(20);
    ALTER TABLE match ADD COLUMN IF NOT EXISTS best_of INTEGER;
    ALTER TABLE match ADD COLUMN IF NOT EXISTS p1_hand VARCHAR(5);
    ALTER TABLE match ADD COLUMN IF NOT EXISTS p2_hand VARCHAR(5);
  `);

    console.log('Fetching match metadata CSV...');
    const csvText = await fetchCSV(CSV_URL);
    const matches = parseCSV(csvText);
    console.log(`Parsed ${matches.length} matches from CSV.`);

    let updated = 0;
    let skipped = 0;

    for (const m of matches) {
        const matchId = m['match_id'];
        if (!matchId) { skipped++; continue; }

        const parts = matchId.split('-');
        if (parts.length < 6) { skipped++; continue; }

        const dateStr = parts[0];
        // Player names in our DB keep underscores, so DON'T replace them
        const p1 = parts[parts.length - 2];
        const p2 = parts[parts.length - 1];

        const dateFormatted = `${dateStr.slice(0, 4)}-${dateStr.slice(4, 6)}-${dateStr.slice(6, 8)}`;
        const surface = m['Surface'] || null;
        const bestOf = m['Best of'] ? parseInt(m['Best of']) : null;
        const p1Hand = m['Pl 1 hand'] || null;
        const p2Hand = m['Pl 2 hand'] || null;

        if (!surface) { skipped++; continue; }

        try {
            const result = await pool.query(`
        UPDATE match 
        SET surface = $1, best_of = $2, p1_hand = $3, p2_hand = $4
        WHERE date = $5::date
          AND first_player_name = $6
          AND second_player_name = $7
      `, [surface, bestOf, p1Hand, p2Hand, dateFormatted, p1, p2]);

            if (result.rowCount > 0) updated++;
        } catch (err) {
            // Skip individual errors silently
        }
    }

    console.log(`Updated ${updated} matches with surface data. Skipped ${skipped}.`);

    const stats = await pool.query(`
    SELECT COALESCE(surface, 'Unknown') as surface, COUNT(*) as cnt 
    FROM match 
    GROUP BY surface 
    ORDER BY cnt DESC
  `);
    console.log('\nSurface distribution:');
    for (const row of stats.rows) {
        console.log(`  ${row.surface}: ${row.cnt}`);
    }

    await pool.end();
    console.log('\nDone!');
}

run().catch(err => {
    console.error('Setup failed:', err);
    process.exit(1);
});
