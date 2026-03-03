/**
 * PostgreSQL CSV Ingestion — Checkpoint 2
 * 
 * Loads all 16 Sackmann charting CSVs into PostgreSQL tables.
 * Creates canonical player_id slugs, verifies cross-table joins.
 * 
 * Run: node scripts/ingest/build_postgres.js
 */

require('dotenv').config();
const fs = require('fs');
const path = require('path');
const { Pool } = require('pg');

const pool = new Pool({
    user: process.env.DB_USER,
    password: process.env.DB_PASSWORD,
    host: process.env.DB_HOST,
    port: process.env.DB_PORT,
    database: process.env.DB_NAME,
});

const RAW_DIR = path.join(__dirname, '..', '..', 'data', 'raw', 'charting');

// CSV -> table config
const TABLES = [
    { file: 'charting-m-stats-Overview.csv', table: 'cs_overview', playerCol: 'player' },
    { file: 'charting-m-stats-ServeBasics.csv', table: 'cs_serve_basics', playerCol: 'player' },
    { file: 'charting-m-stats-ServeDirection.csv', table: 'cs_serve_direction', playerCol: 'player' },
    { file: 'charting-m-stats-ServeInfluence.csv', table: 'cs_serve_influence', playerCol: 'player' },
    { file: 'charting-m-stats-ShotTypes.csv', table: 'cs_shot_types', playerCol: 'player' },
    { file: 'charting-m-stats-ShotDirection.csv', table: 'cs_shot_direction', playerCol: 'player' },
    { file: 'charting-m-stats-ShotDirOutcomes.csv', table: 'cs_shot_dir_outcomes', playerCol: 'player' },
    { file: 'charting-m-stats-Rally.csv', table: 'cs_rally', playerCol: null },     // uses server/returner
    { file: 'charting-m-stats-ReturnDepth.csv', table: 'cs_return_depth', playerCol: 'player' },
    { file: 'charting-m-stats-ReturnOutcomes.csv', table: 'cs_return_outcomes', playerCol: 'player' },
    { file: 'charting-m-stats-NetPoints.csv', table: 'cs_net_points', playerCol: 'player' },
    { file: 'charting-m-stats-KeyPointsServe.csv', table: 'cs_key_points_serve', playerCol: 'player' },
    { file: 'charting-m-stats-KeyPointsReturn.csv', table: 'cs_key_points_return', playerCol: 'player' },
    { file: 'charting-m-stats-SnV.csv', table: 'cs_serve_and_volley', playerCol: 'player' },
    { file: 'charting-m-stats-SvBreakSplit.csv', table: 'cs_sv_break_split', playerCol: 'player' },
    { file: 'charting-m-stats-SvBreakTotal.csv', table: 'cs_sv_break_total', playerCol: 'player' },
];

function parseCSV(filePath) {
    const content = fs.readFileSync(filePath, 'utf-8');
    const lines = content.split('\n').filter(l => l.trim());
    const headers = lines[0].split(',').map(h => h.trim().toLowerCase().replace(/\+/g, '_plus_'));
    const rows = [];
    for (let i = 1; i < lines.length; i++) {
        const vals = lines[i].split(',');
        const row = {};
        for (let j = 0; j < headers.length; j++) {
            row[headers[j]] = (vals[j] || '').trim();
        }
        rows.push(row);
    }
    return { headers, rows };
}

function makePlayerId(name) {
    if (!name) return '';
    return name.trim().toLowerCase()
        .replace(/[^\w\s]/g, '')
        .replace(/\s+/g, '_');
}

function inferSqlType(header, sampleValues) {
    // These are always text
    if (['match_id', 'player', 'row', 'set', 'server', 'returner', 'player_id', 'server_id', 'returner_id'].includes(header)) {
        return 'TEXT';
    }
    // Check if all non-empty values are integers
    const nonEmpty = sampleValues.filter(v => v !== '' && v !== null && v !== undefined);
    if (nonEmpty.length === 0) return 'TEXT';

    const allInt = nonEmpty.every(v => /^-?\d+$/.test(v));
    if (allInt) return 'INTEGER';

    const allFloat = nonEmpty.every(v => /^-?\d+\.?\d*$/.test(v));
    if (allFloat) return 'REAL';

    return 'TEXT';
}

async function run() {
    const client = await pool.connect();

    try {
        console.log('=== PostgreSQL CSV Ingestion ===\n');

        // 1. Load each CSV into a table
        for (const { file, table, playerCol } of TABLES) {
            const filePath = path.join(RAW_DIR, file);
            if (!fs.existsSync(filePath)) {
                console.log(`  ⚠ SKIPPED ${file}`);
                continue;
            }

            const { headers, rows } = parseCSV(filePath);

            // Add player_id column(s)
            const extraCols = [];
            if (playerCol === 'player') {
                extraCols.push('player_id');
            } else if (table === 'cs_rally') {
                extraCols.push('server_id', 'returner_id');
            }
            const allHeaders = [...headers, ...extraCols];

            // Infer types from first 100 rows
            const sampleRows = rows.slice(0, 100);
            const colTypes = {};
            for (const h of headers) {
                colTypes[h] = inferSqlType(h, sampleRows.map(r => r[h]));
            }
            for (const ec of extraCols) {
                colTypes[ec] = 'TEXT';
            }

            // Create table
            await client.query(`DROP TABLE IF EXISTS ${table} CASCADE`);
            const colDefs = allHeaders.map(h => `"${h}" ${colTypes[h] || 'TEXT'}`).join(', ');
            await client.query(`CREATE TABLE ${table} (${colDefs})`);

            // Batch insert (500 at a time)
            const batchSize = 500;
            let inserted = 0;
            for (let b = 0; b < rows.length; b += batchSize) {
                const batch = rows.slice(b, b + batchSize);
                const placeholders = [];
                const values = [];
                let paramIdx = 1;

                for (const row of batch) {
                    const rowPlaceholders = [];
                    for (const h of headers) {
                        let val = row[h];
                        if (val === '' || val === undefined) val = null;
                        else if (colTypes[h] === 'INTEGER') val = parseInt(val) || null;
                        else if (colTypes[h] === 'REAL') val = parseFloat(val) || null;
                        values.push(val);
                        rowPlaceholders.push(`$${paramIdx++}`);
                    }
                    // Add player_id
                    if (playerCol === 'player') {
                        values.push(makePlayerId(row.player));
                        rowPlaceholders.push(`$${paramIdx++}`);
                    } else if (table === 'cs_rally') {
                        values.push(makePlayerId(row.server));
                        rowPlaceholders.push(`$${paramIdx++}`);
                        values.push(makePlayerId(row.returner));
                        rowPlaceholders.push(`$${paramIdx++}`);
                    }
                    placeholders.push(`(${rowPlaceholders.join(',')})`);
                }

                const colNames = allHeaders.map(h => `"${h}"`).join(',');
                await client.query(`INSERT INTO ${table} (${colNames}) VALUES ${placeholders.join(',')}`, values);
                inserted += batch.length;
            }

            console.log(`  ✓ ${table}: ${inserted.toLocaleString()} rows, ${allHeaders.length} cols`);
        }

        // 2. Create canonical players table
        console.log('\nBuilding cs_players table...');
        await client.query('DROP TABLE IF EXISTS cs_players CASCADE');
        await client.query(`
            CREATE TABLE cs_players (
                player_id TEXT PRIMARY KEY,
                player_name_raw TEXT,
                player_name_display TEXT,
                last_name TEXT
            )
        `);

        const playerResult = await client.query(`
            SELECT DISTINCT player_id, player 
            FROM cs_overview 
            WHERE player_id IS NOT NULL AND player_id != ''
        `);

        let pCount = 0;
        for (const r of playerResult.rows) {
            const parts = r.player.trim().split(' ');
            const lastName = parts[parts.length - 1];
            try {
                await client.query(
                    'INSERT INTO cs_players VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING',
                    [r.player_id, r.player, r.player.trim(), lastName]
                );
                pCount++;
            } catch (e) { /* skip */ }
        }
        console.log(`  ✓ cs_players: ${pCount} players\n`);

        // 3. Create indexes for fast joins
        console.log('Creating indexes...');
        for (const { table, playerCol } of TABLES) {
            try {
                await client.query(`CREATE INDEX IF NOT EXISTS idx_${table}_mid ON ${table} (match_id)`);
                if (playerCol === 'player') {
                    await client.query(`CREATE INDEX IF NOT EXISTS idx_${table}_pid ON ${table} (player_id)`);
                }
            } catch (e) { /* ok */ }
        }
        console.log('  ✓ Indexes created\n');

        // 4. Verification joins
        console.log('=== Verification ===');

        const j1 = await client.query(`
            SELECT COUNT(*) FROM cs_overview o
            INNER JOIN cs_serve_basics sb ON o.match_id = sb.match_id AND o.player_id = sb.player_id
            WHERE o.set = 'Total' AND sb.row = 'Total'
        `);
        console.log(`  Overview ⟕ ServeBasics (Total): ${parseInt(j1.rows[0].count).toLocaleString()} rows`);

        const j2 = await client.query(`
            SELECT COUNT(*) FROM cs_overview o
            INNER JOIN cs_serve_basics sb ON o.match_id = sb.match_id AND o.player_id = sb.player_id
            INNER JOIN cs_net_points np ON o.match_id = np.match_id AND o.player_id = np.player_id
            WHERE o.set = 'Total' AND sb.row = 'Total' AND np.row = 'NetPoints'
        `);
        console.log(`  Overview ⟕ ServeBasics ⟕ NetPoints: ${parseInt(j2.rows[0].count).toLocaleString()} rows`);

        // 5. Sample player
        const sample = await client.query(`
            SELECT o.player, o.serve_pts, o.aces, o.dfs, o.first_in, o.first_won,
                   o.second_in, o.second_won, o.winners_fh, o.winners_bh, 
                   o.unforced_fh, o.unforced_bh, o.bk_pts, o.bp_saved,
                   o.return_pts, o.return_pts_won,
                   sb.unret, sb.pts_won_lte_3_shots,
                   np.net_pts, np.pts_won as net_won
            FROM cs_overview o
            INNER JOIN cs_serve_basics sb ON o.match_id = sb.match_id AND o.player_id = sb.player_id
            INNER JOIN cs_net_points np ON o.match_id = np.match_id AND o.player_id = np.player_id
            WHERE o.set = 'Total' AND sb.row = 'Total' AND np.row = 'NetPoints'
              AND o.player_id = 'carlos_alcaraz'
            LIMIT 2
        `);
        console.log(`\n  Sample: Carlos Alcaraz (${sample.rows.length} matches)`);
        if (sample.rows[0]) {
            const r = sample.rows[0];
            console.log(`    serve_pts=${r.serve_pts}, aces=${r.aces}, dfs=${r.dfs}`);
            console.log(`    first_in=${r.first_in}, first_won=${r.first_won}`);
            console.log(`    second_in=${r.second_in}, second_won=${r.second_won}`);
            console.log(`    winners_fh=${r.winners_fh}, winners_bh=${r.winners_bh}`);
            console.log(`    unforced_fh=${r.unforced_fh}, unforced_bh=${r.unforced_bh}`);
            console.log(`    net_pts=${r.net_pts}, net_won=${r.net_won}`);
            console.log(`    bk_pts=${r.bk_pts}, bp_saved=${r.bp_saved}`);
            console.log(`    return_pts=${r.return_pts}, return_pts_won=${r.return_pts_won}`);
        }

        console.log('\n✅ Checkpoint 2 complete!');
    } catch (err) {
        console.error('Error:', err.message);
        throw err;
    } finally {
        client.release();
        await pool.end();
    }
}

run().catch(() => process.exit(1));
