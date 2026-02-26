const { Pool } = require('pg');
const pool = new Pool({ user: 'postgres', password: '139071', host: 'localhost', port: 5432, database: 'tennis' });

async function run() {
    console.log("Adding player_won column to point table...");
    await pool.query('ALTER TABLE point ADD COLUMN IF NOT EXISTS player_won INTEGER;');

    console.log("Grabbing all points and server info...");
    const res = await pool.query(`
        SELECT p.match_id, p.number, p.game_score, p.set1, p.set2, p.gm1, p.gm2,
          CASE WHEN ((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0)) THEN 1 ELSE 2 END AS server_num
        FROM point p
        JOIN (
            SELECT m.id, 1 AS player_num FROM match m
            UNION ALL
            SELECT m.id, 2 AS player_num FROM match m
        ) mp ON p.match_id = mp.id AND mp.player_num = 1 -- We just need to anchor to player 1's service pattern
        ORDER BY p.match_id, p.number
    `);

    console.log("Calculating point winners...");
    const points = res.rows;
    let updates = [];

    for (let i = 0; i < points.length; i++) {
        const pt = points[i];
        let playerWon = null;

        if (i + 1 < points.length && points[i + 1].match_id === pt.match_id) {
            const nextPt = points[i + 1];

            if (nextPt.gm1 > pt.gm1 || nextPt.set1 > pt.set1) {
                playerWon = 1;
            } else if (nextPt.gm2 > pt.gm2 || nextPt.set2 > pt.set2) {
                playerWon = 2;
            } else {
                const scoresToInt = (scoreStr) => {
                    const parts = scoreStr.split('-');
                    if (parts.length !== 2) return [0, 0];
                    const map = { '0': 0, '15': 1, '30': 2, '40': 3, 'AD': 4 };
                    return [map[parts[0]] || 0, map[parts[1]] || 0];
                };
                const [currS, currR] = scoresToInt(pt.game_score);
                const [nextS, nextR] = scoresToInt(nextPt.game_score);

                if (nextS > currS && nextR <= currR) {
                    playerWon = pt.server_num;
                } else if (nextR > currR && nextS <= currS) {
                    playerWon = pt.server_num === 1 ? 2 : 1;
                }
            }
        } else {
            // Last point of the match! Assume the global winner of the match won it based on set score
            playerWon = pt.set1 > pt.set2 ? 1 : 2;
        }

        if (playerWon !== null) {
            updates.push({ match_id: pt.match_id, number: pt.number, player_won: playerWon });
        }
    }

    console.log(`Updating ${updates.length} points...`);

    // Batch update via CTE
    const client = await pool.connect();
    try {
        await client.query('BEGIN');

        let valuesQuery = updates.map((u, idx) => `('${u.match_id}', ${u.number}, ${u.player_won})`).join(',');

        // Use a temporary table for massive speedup
        await client.query(`
            CREATE TEMP TABLE temp_point_winners (
                match_id UUID,
                number INTEGER,
                player_won INTEGER
            ) ON COMMIT DROP;
        `);

        await client.query(`
            INSERT INTO temp_point_winners(match_id, number, player_won)
            VALUES ${valuesQuery};
        `);

        await client.query(`
            UPDATE point
            SET player_won = tw.player_won
            FROM temp_point_winners tw
            WHERE point.match_id = tw.match_id AND point.number = tw.number;
        `);

        await client.query('COMMIT');
        console.log("Successfully updated point winners.");
    } catch (e) {
        await client.query('ROLLBACK');
        console.error(e);
    } finally {
        client.release();
    }
    pool.end();
}
run();
