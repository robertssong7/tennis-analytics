const { Pool } = require('pg');
const pool = new Pool({ user: 'postgres', password: '139071', host: 'localhost', port: 5432, database: 'tennis' });

async function run() {
    try {
        const res = await pool.query(`
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'point'
        `);
        console.log(JSON.stringify(res.rows.map(r => r.column_name), null, 2));
    } catch (err) {
        console.error(err);
    } finally {
        pool.end();
    }
}
run();
