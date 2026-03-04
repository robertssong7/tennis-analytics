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
    const players = ['pete_sampras', 'bjorn_borg', 'boris_becker', 'andre_agassi'];

    for (const pid of players) {
        const res = await pool.query(
            `SELECT player_id, player, match_id, serve_pts, return_pts, first_in, first_won
             FROM cs_overview 
             WHERE player_id = $1 AND set = 'Total'
             ORDER BY match_id`,
            [pid]
        );

        console.log(`\n=== ${pid} (${res.rows.length} match rows) ===`);
        for (const row of res.rows) {
            console.log(`  ${row.match_id} | serve_pts=${row.serve_pts} ret_pts=${row.return_pts} 1st_in=${row.first_in} 1st_won=${row.first_won}`);
        }
    }

    await pool.end();
}

run().catch(e => { console.error(e); pool.end(); });
