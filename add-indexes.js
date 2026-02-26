/**
 * Adds performance indexes to the tennis database.
 * Run once: node add-indexes.js
 */
const { Pool } = require('pg');
const pool = new Pool({ user: 'postgres', password: '139071', host: 'localhost', port: 5432, database: 'tennis' });

async function run() {
    console.log('Adding performance indexes...');

    const indexes = [
        'CREATE INDEX IF NOT EXISTS idx_point_match_id ON point(match_id)',
        'CREATE INDEX IF NOT EXISTS idx_point_player_won ON point(player_won)',
        'CREATE INDEX IF NOT EXISTS idx_point_match_number ON point(match_id, number)',
        'CREATE INDEX IF NOT EXISTS idx_shot_point ON shot(point_match_id, point_number)',
        'CREATE INDEX IF NOT EXISTS idx_shot_point_num ON shot(point_match_id, point_number, number)',
        'CREATE INDEX IF NOT EXISTS idx_match_p1 ON match(first_player_name)',
        'CREATE INDEX IF NOT EXISTS idx_match_p2 ON match(second_player_name)',
    ];

    for (const sql of indexes) {
        try {
            console.log(`  Running: ${sql.substring(0, 60)}...`);
            await pool.query(sql);
            console.log('  ✓ Done');
        } catch (err) {
            console.error(`  ✗ Error: ${err.message}`);
        }
    }

    console.log('\nAll indexes created!');
    pool.end();
}

run();
