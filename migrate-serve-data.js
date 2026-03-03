/**
 * Serve Stats Migration
 * 
 * Downloads pre-computed serve stats from JeffSackmann's ServeBasics CSV
 * and generates per-player serve-detailed JSON files.
 * 
 * The CSV has rows with:
 *   match_id, player, row (Total/1/2), pts, pts_won, aces, unret, forced_err, ...
 * 
 * Row "1" = 1st serve stats, Row "2" = 2nd serve stats, Row "Total" = combined.
 * Double faults = Total pts - (1st serve pts + 2nd serve pts that went in)
 *   Actually: Total serve pts = 1st serve attempts.
 *             2nd serve pts = faulted 1st serves → 2nd attempt.
 *             1st serve in = Total pts - 2nd serve pts.
 *             Double faults = need to compute from missing pts.
 *
 * Per the JeffSackmann encoding:
 *   Row "Total": total serve points played (total pts where this player served)
 *   Row "1": 1st serve points that went IN (and were played)
 *   Row "2": 2nd serve points (1st serve was a fault, 2nd serve was attempted)
 *     - The "pts" in row 2 = 2nd serve attempts
 *     - pts_won in row 2 = points won on 2nd serve
 *   
 *   1st serve in = row "1" pts
 *   1st serve attempts = row "Total" pts (every point starts with a 1st serve)
 *   2nd serve points = row "2" pts (these are the points where 1st serve faulted)
 *   1st serve in % = row1.pts / rowTotal.pts
 *   Double faults = rowTotal.pts - row1.pts - row2.pts
 *     (total serve points - 1st serve in - 2nd serve in = missed both)
 */

const https = require('https');
const fs = require('fs');
const path = require('path');

require('dotenv').config();
const { Pool } = require('pg');
const pool = new Pool({
    user: process.env.DB_USER,
    password: process.env.DB_PASSWORD,
    host: process.env.DB_HOST,
    port: process.env.DB_PORT,
    database: process.env.DB_NAME,
});

const CSV_URL = 'https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-m-stats-ServeBasics.csv';
const DATA_DIR = path.join(__dirname, 'docs', 'data', 'players');

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

function parseCSV(text) {
    const lines = text.split('\n');
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
    console.log('=== SERVE STATS MIGRATION ===\n');

    // 1. Fetch the CSV
    console.log('Step 1: Fetching ServeBasics CSV...');
    const csvText = await fetchCSV(CSV_URL);
    const rows = parseCSV(csvText);
    console.log(`  Parsed ${rows.length} rows.\n`);

    // 2. Get all match IDs from our DB to build a lookup
    console.log('Step 2: Loading matches from DB...');
    const dbMatches = await pool.query(`
        SELECT id, date, first_player_name, second_player_name, event, surface
        FROM match
    `);

    // Build lookup: "YYYYMMDD-P1-P2" → match row
    const matchLookup = {};
    for (const m of dbMatches.rows) {
        const dateStr = m.date ? new Date(m.date).toISOString().slice(0, 10).replace(/-/g, '') : '';
        const key1 = `${dateStr}-${m.first_player_name}-${m.second_player_name}`;
        const key2 = `${dateStr}-${m.second_player_name}-${m.first_player_name}`;
        matchLookup[key1] = m;
        matchLookup[key2] = m;
    }
    console.log(`  Loaded ${dbMatches.rows.length} matches.\n`);

    // 3. Group CSV rows by player
    console.log('Step 3: Aggregating per-player serve stats...');
    const playerStats = {}; // playerName -> { total, first, second, aces, dfs, ... }
    const playerSurfaceStats = {}; // playerName -> surface -> stats

    let matched = 0, unmatched = 0;

    for (const row of rows) {
        // Parse match_id to extract date and player names
        const csvMatchId = row.match_id;
        if (!csvMatchId) continue;

        const parts = csvMatchId.split('-');
        if (parts.length < 6) continue;

        const dateStr = parts[0];
        const p1 = parts[parts.length - 2];
        const p2 = parts[parts.length - 1];

        // Try to find this match in our DB
        const key = `${dateStr}-${p1}-${p2}`;
        const dbMatch = matchLookup[key];

        if (!dbMatch) { unmatched++; continue; }
        matched++;

        // Normalize player name: CSV has "First Last", DB has "First_Last"
        const playerName = row.player.replace(/ /g, '_');
        const surface = dbMatch.surface || 'Unknown';

        // Initialize player stats if needed
        if (!playerStats[playerName]) {
            playerStats[playerName] = {
                totalServePoints: 0, firstServeIn: 0, firstServeWon: 0,
                secondServeTotal: 0, secondServeWon: 0,
                aces: 0, doubleFaults: 0,
                unreturnedServes: 0, forcedErrors: 0
            };
        }
        if (!playerSurfaceStats[playerName]) playerSurfaceStats[playerName] = {};
        if (!playerSurfaceStats[playerName][surface]) {
            playerSurfaceStats[playerName][surface] = {
                totalServePoints: 0, firstServeIn: 0, firstServeWon: 0,
                secondServeTotal: 0, secondServeWon: 0,
                aces: 0, doubleFaults: 0,
                unreturnedServes: 0, forcedErrors: 0
            };
        }

        const pts = parseInt(row.pts) || 0;
        const ptsWon = parseInt(row.pts_won) || 0;
        const aces = parseInt(row.aces) || 0;
        const unret = parseInt(row.unret) || 0;
        const forcedErr = parseInt(row.forced_err) || 0;
        const rowType = row.row; // "Total", "1", or "2"

        const targets = [playerStats[playerName], playerSurfaceStats[playerName][surface]];

        for (const s of targets) {
            if (rowType === 'Total') {
                s.totalServePoints += pts;
                s.aces += aces;
                s.unreturnedServes += unret;
            } else if (rowType === '1') {
                s.firstServeIn += pts;  // row 1 pts = points where 1st serve went in
                s.firstServeWon += ptsWon;
                s.forcedErrors += forcedErr; // forced errors on 1st serve return
            } else if (rowType === '2') {
                s.secondServeTotal += pts;  // row 2 pts = 2nd serve attempts
                s.secondServeWon += ptsWon;
            }
        }
    }

    // Compute double faults: total points - 1st serve in - 2nd serve in
    // DFs = points where both serves faulted = totalServePoints - firstServeIn - secondServeTotal
    for (const pName of Object.keys(playerStats)) {
        const s = playerStats[pName];
        s.doubleFaults = Math.max(0, s.totalServePoints - s.firstServeIn - s.secondServeTotal);

        if (playerSurfaceStats[pName]) {
            for (const surface of Object.keys(playerSurfaceStats[pName])) {
                const ss = playerSurfaceStats[pName][surface];
                ss.doubleFaults = Math.max(0, ss.totalServePoints - ss.firstServeIn - ss.secondServeTotal);
            }
        }
    }

    console.log(`  Matched ${matched} CSV rows, unmatched ${unmatched}.`);
    console.log(`  Found serve data for ${Object.keys(playerStats).length} players.\n`);

    // 4. Write serve-detailed JSON files
    console.log('Step 4: Writing serve-detailed JSON files...');
    let written = 0;

    for (const [playerName, stats] of Object.entries(playerStats)) {
        const playerDir = path.join(DATA_DIR, playerName);
        if (!fs.existsSync(playerDir)) continue;

        // Compute derived stats
        const serveData = computeServeDetailed(stats);

        // Write "all" surface file
        fs.writeFileSync(path.join(playerDir, 'serve-detailed.json'), JSON.stringify(serveData, null, 2));
        written++;

        // Write per-surface files
        if (playerSurfaceStats[playerName]) {
            for (const [surface, surfStats] of Object.entries(playerSurfaceStats[playerName])) {
                if (surface === 'Unknown' || !surface) continue;
                const surfDir = path.join(playerDir, 'surface', surface);
                if (!fs.existsSync(surfDir)) { fs.mkdirSync(surfDir, { recursive: true }); }
                const surfData = computeServeDetailed(surfStats);
                fs.writeFileSync(path.join(surfDir, 'serve-detailed.json'), JSON.stringify(surfData, null, 2));
            }
        }
    }

    console.log(`  Wrote serve-detailed.json for ${written} players.\n`);

    // 5. Sample check
    console.log('=== SAMPLE: Carlos Alcaraz ===');
    const alcaraz = playerStats['Carlos_Alcaraz'];
    if (alcaraz) {
        const d = computeServeDetailed(alcaraz);
        console.log(`  Total serve points:  ${d.totalServePoints}`);
        console.log(`  1st serve in:        ${d.firstServeIn} (${(d.firstServeInPct * 100).toFixed(1)}%)`);
        console.log(`  1st serve won:       ${d.firstServeWon} (${(d.firstServeWinPct * 100).toFixed(1)}%)`);
        console.log(`  2nd serve total:     ${d.secondServeTotal}`);
        console.log(`  2nd serve won:       ${d.secondServeWon} (${(d.secondServeWinPct * 100).toFixed(1)}%)`);
        console.log(`  Aces:                ${d.aces} (${(d.acePct * 100).toFixed(1)}%)`);
        console.log(`  Double faults:       ${d.doubleFaults} (${(d.doubleFaultPct * 100).toFixed(1)}%)`);
    }

    await pool.end();
    console.log('\n✅ Migration complete!');
}

function computeServeDetailed(stats) {
    const s = stats;
    return {
        totalServePoints: s.totalServePoints,
        firstServeIn: s.firstServeIn,
        firstServeInPct: s.totalServePoints > 0 ? s.firstServeIn / s.totalServePoints : 0,
        firstServeWon: s.firstServeWon,
        firstServeWinPct: s.firstServeIn > 0 ? s.firstServeWon / s.firstServeIn : 0,
        secondServeTotal: s.secondServeTotal,
        secondServeWon: s.secondServeWon,
        secondServeWinPct: s.secondServeTotal > 0 ? s.secondServeWon / s.secondServeTotal : 0,
        aces: s.aces,
        acePct: s.totalServePoints > 0 ? s.aces / s.totalServePoints : 0,
        doubleFaults: s.doubleFaults,
        doubleFaultPct: s.totalServePoints > 0 ? s.doubleFaults / s.totalServePoints : 0
    };
}

run().catch(err => { console.error(err); process.exit(1); });
