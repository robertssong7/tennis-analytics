/**
 * Migration: Add score_state_label column to point table and backfill
 * using the shared score state taxonomy.
 * 
 * Run once: node scripts/migrate-score-state.js
 */
require('dotenv').config();
const { Pool } = require('pg');
const { classifyScoreState } = require('./utils/score-utils');

const pool = new Pool({
    user: process.env.DB_USER,
    password: process.env.DB_PASSWORD,
    host: process.env.DB_HOST,
    port: process.env.DB_PORT,
    database: process.env.DB_NAME,
});

async function run() {
    console.log('=== Score State Migration ===');

    // 1. Add column
    console.log('Adding score_state_label column...');
    await pool.query('ALTER TABLE point ADD COLUMN IF NOT EXISTS score_state_label VARCHAR(30);');

    // 2. Load all points grouped by match (ordered by point number)
    console.log('Loading all points...');
    const res = await pool.query(`
        SELECT p.match_id, p.number, p.game_score, p.gm1, p.gm2,
               CASE WHEN ((1 = 1 AND p.number % 2 = 1) OR (2 = 2 AND p.number % 2 = 0)) THEN true ELSE false END AS p1_is_server,
               m.first_player_name, m.second_player_name
        FROM point p
        JOIN match m ON p.match_id = m.id
        WHERE p.score_state_label IS NULL
        ORDER BY p.match_id, p.number
    `);

    console.log(`Processing ${res.rows.length} points...`);

    // Group by match
    const matchGroups = {};
    for (const row of res.rows) {
        if (!matchGroups[row.match_id]) matchGroups[row.match_id] = [];
        matchGroups[row.match_id].push(row);
    }

    const updates = [];
    for (const [matchId, points] of Object.entries(matchGroups)) {
        for (const pt of points) {
            // For classification, we need server games and receiver games.
            // gm1/gm2 represent player 1 and player 2 games in the current set
            // We determine who is serving from the point number (odd = p1 serves)
            const p1Serving = pt.number % 2 === 1;

            // Games from server's perspective
            const serverGames = p1Serving ? (pt.gm1 || 0) : (pt.gm2 || 0);
            const receiverGames = p1Serving ? (pt.gm2 || 0) : (pt.gm1 || 0);

            // For the target player (always player 1 for this classification),
            // is the target player the server?
            const isPlayerServer = p1Serving;

            const label = classifyScoreState(pt.game_score, serverGames, receiverGames, isPlayerServer);
            updates.push({ match_id: matchId, number: pt.number, label });
        }
    }

    // Batch update
    console.log(`Updating ${updates.length} points with score_state_label...`);

    const client = await pool.connect();
    try {
        await client.query('BEGIN');

        // Create temp table for batch update
        await client.query(`
            CREATE TEMP TABLE temp_score_states (
                match_id UUID,
                number INTEGER,
                score_state_label VARCHAR(30)
            ) ON COMMIT DROP
        `);

        // Insert in batches of 5000
        const BATCH_SIZE = 5000;
        for (let i = 0; i < updates.length; i += BATCH_SIZE) {
            const batch = updates.slice(i, i + BATCH_SIZE);
            const values = batch
                .map(u => `('${u.match_id}', ${u.number}, '${u.label}')`)
                .join(',');
            await client.query(`INSERT INTO temp_score_states(match_id, number, score_state_label) VALUES ${values}`);
        }

        await client.query(`
            UPDATE point p
            SET score_state_label = ts.score_state_label
            FROM temp_score_states ts
            WHERE p.match_id = ts.match_id AND p.number = ts.number
        `);

        await client.query('COMMIT');
        console.log('Score state labels updated successfully.');
    } catch (e) {
        await client.query('ROLLBACK');
        console.error('Batch update failed:', e);
    } finally {
        client.release();
    }

    // Verify
    const stats = await pool.query(`
        SELECT score_state_label, COUNT(*) AS cnt
        FROM point
        WHERE score_state_label IS NOT NULL
        GROUP BY score_state_label
        ORDER BY cnt DESC
    `);
    console.log('\nScore state distribution:');
    for (const r of stats.rows) {
        console.log(`  ${r.score_state_label}: ${r.cnt}`);
    }

    await pool.end();
    console.log('Done!');
}

run().catch(err => {
    console.error('Migration failed:', err);
    process.exit(1);
});
