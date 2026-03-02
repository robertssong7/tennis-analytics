/**
 * Migration: Add rally_length column to point table and backfill
 * from shot count per point.
 * 
 * Run once: node scripts/migrate-rally-length.js
 */
require('dotenv').config();
const { Pool } = require('pg');

const pool = new Pool({
    user: process.env.DB_USER,
    password: process.env.DB_PASSWORD,
    host: process.env.DB_HOST,
    port: process.env.DB_PORT,
    database: process.env.DB_NAME,
});

async function run() {
    console.log('=== Rally Length Migration ===');

    // 1. Add column
    console.log('Adding rally_length column...');
    await pool.query('ALTER TABLE point ADD COLUMN IF NOT EXISTS rally_length SMALLINT;');

    // 2. Backfill: count shots per point
    console.log('Backfilling rally_length from shot count...');
    const result = await pool.query(`
        UPDATE point p
        SET rally_length = sub.shot_count
        FROM (
            SELECT point_match_id, point_number, COUNT(*) AS shot_count
            FROM shot
            GROUP BY point_match_id, point_number
        ) sub
        WHERE p.match_id = sub.point_match_id
          AND p.number = sub.point_number
          AND p.rally_length IS NULL
    `);

    console.log(`Updated ${result.rowCount} points with rally_length.`);

    // 3. Verify
    const stats = await pool.query(`
        SELECT
            COUNT(*) AS total_points,
            COUNT(rally_length) AS with_rally_length,
            AVG(rally_length)::NUMERIC(5,2) AS avg_rally,
            MIN(rally_length) AS min_rally,
            MAX(rally_length) AS max_rally
        FROM point
    `);
    console.log('Stats:', stats.rows[0]);

    await pool.end();
    console.log('Done!');
}

run().catch(err => {
    console.error('Migration failed:', err);
    process.exit(1);
});
