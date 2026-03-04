// Check match table and winner determination
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
    // 1. match table columns
    const cols = await pool.query(`
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_name = 'match' ORDER BY ordinal_position
    `);
    console.log('=== match table columns ===');
    cols.rows.forEach(r => console.log('  ' + r.column_name + ' (' + r.data_type + ')'));

    // 2. Sample match rows
    const sample = await pool.query(`SELECT * FROM match LIMIT 5`);
    console.log('\n=== Sample match rows ===');
    sample.rows.forEach(r => console.log('  ' + JSON.stringify(r)));

    // 3. Verify winner from match_id
    // Known: 1990 US Open F - Sampras beat Agassi
    // match_id: 19900909-M-US_Open-F-Andre_Agassi-Pete_Sampras
    // If Player1 (Agassi) is NOT the winner, then winner is Player2

    // Let's check another known result: 2001 Wimbledon R16 - Federer beat Sampras
    // We saw: 20010702-M-Wimbledon-R16-Roger_Federer-Pete_Sampras
    // Federer IS the winner, so Player1 = winner? 

    // But 1990 US Open F: Sampras beat Agassi, match_id has Agassi first...
    // So Player1 is NOT always the winner.

    // Let's check more matches with known outcomes
    console.log('\n=== Known outcome checks ===');

    // Can we determine winner from set scores?
    // Check cs_overview for set-level data
    const setData = await pool.query(`
        SELECT match_id, player, set, serve_pts, first_won, second_won, return_pts_won
        FROM cs_overview
        WHERE match_id = '19900909-M-US_Open-F-Andre_Agassi-Pete_Sampras'
        AND set ~ '^[0-9]+$'
        ORDER BY player, set
    `);
    console.log('\n=== Set-level data: 1990 US Open F (Sampras won) ===');
    setData.rows.forEach(r => {
        var totalWon = (parseInt(r.first_won) || 0) + (parseInt(r.second_won) || 0) + (parseInt(r.return_pts_won) || 0);
        console.log('  ' + r.player + ' set' + r.set + ': serve_pts=' + r.serve_pts + ' total_won~' + totalWon);
    });

    // 4. Check cs_players table
    const playerCols = await pool.query(`
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_name = 'cs_players' ORDER BY ordinal_position
    `);
    console.log('\n=== cs_players columns ===');
    playerCols.rows.forEach(r => console.log('  ' + r.column_name + ' (' + r.data_type + ')'));

    const playerSample = await pool.query(`SELECT * FROM cs_players LIMIT 5`);
    console.log('\n=== cs_players sample ===');
    playerSample.rows.forEach(r => console.log('  ' + JSON.stringify(r)));

    // 5. Check point table 
    const pointCols = await pool.query(`
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_name = 'point' ORDER BY ordinal_position
    `);
    console.log('\n=== point table columns ===');
    pointCols.rows.forEach(r => console.log('  ' + r.column_name + ' (' + r.data_type + ')'));

    const pointSample = await pool.query(`SELECT * FROM point LIMIT 3`);
    console.log('\n=== point sample ===');
    pointSample.rows.forEach(r => console.log('  ' + JSON.stringify(r)));

    // 6. Can we infer winner from Total serve/return points won?
    const totalData = await pool.query(`
        SELECT match_id, player, serve_pts, first_won, second_won, return_pts_won
        FROM cs_overview
        WHERE match_id = '19900909-M-US_Open-F-Andre_Agassi-Pete_Sampras'
        AND set = 'Total'
    `);
    console.log('\n=== Total stats 1990 US Open F ===');
    totalData.rows.forEach(r => {
        var totalWon = (parseInt(r.first_won) || 0) + (parseInt(r.second_won) || 0) + (parseInt(r.return_pts_won) || 0);
        console.log('  ' + r.player + ': serve_won=' + ((parseInt(r.first_won) || 0) + (parseInt(r.second_won) || 0)) + ' ret_won=' + r.return_pts_won + ' total=' + totalWon);
    });

    await pool.end();
}

run().catch(e => { console.error(e); pool.end(); });
