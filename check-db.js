const { Pool } = require('pg');
const pool = new Pool({
    user: 'postgres',
    password: '139071',
    host: 'localhost',
    port: 5432,
    database: 'tennis',
});

async function check() {
    // Check first 5 matches
    const r1 = await pool.query('SELECT id, date, first_player_name, second_player_name, event FROM match LIMIT 5');
    console.log('Sample matches:');
    console.log(JSON.stringify(r1.rows, null, 2));

    // Check date type
    const r2 = await pool.query("SELECT data_type FROM information_schema.columns WHERE table_name='match' AND column_name='date'");
    console.log('\nDate column type:', r2.rows[0]);

    // Check count
    const r3 = await pool.query('SELECT COUNT(*) FROM match');
    console.log('Total matches:', r3.rows[0].count);

    await pool.end();
}
check();
