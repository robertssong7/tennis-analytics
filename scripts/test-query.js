const { Pool } = require('pg');
require('dotenv').config();

const pool = new Pool({
    user: process.env.DB_USER || 'postgres',
    password: process.env.DB_PASSWORD,
    host: process.env.DB_DOMAIN || process.env.DB_HOST || 'localhost',
    port: process.env.DB_PORT || 5432,
    database: process.env.DB_NAME || 'tennis_analytics'
});

async function run() {
    const result = await pool.query(`
      WITH h2h_matches AS (
        SELECT m.id, m.date, m.event, m.event_round, m.surface,
               m.first_player_name, m.second_player_name
        FROM match m
        WHERE (m.first_player_name = $1 AND m.second_player_name = $2)
           OR (m.first_player_name = $2 AND m.second_player_name = $1)
      ),
      set_ends AS (
        SELECT p.match_id, 
               p.set1 + p.set2 AS curr_set,
               p.gm1, p.gm2, p.game_score, p.player_won,
               ROW_NUMBER() OVER(PARTITION BY p.match_id, p.set1 + p.set2 ORDER BY p.number DESC) as rn
        FROM point p
        JOIN h2h_matches h ON p.match_id = h.id
      ),
      set_scores AS (
        SELECT match_id, curr_set, gm1, gm2, player_won, game_score,
          CASE WHEN player_won=1 THEN (gm1::int)+1 ELSE (gm1::int) END as s1,
          CASE WHEN player_won=2 THEN (gm2::int)+1 ELSE (gm2::int) END as s2,
          CASE WHEN (gm1::int)=6 AND (gm2::int)=6 AND game_score ~ '^[0-9]+-[0-9]+$' THEN
               CASE WHEN player_won=1 THEN (split_part(game_score, '-', 1)::int)+1 ELSE (split_part(game_score, '-', 1)::int) END
          ELSE null END as t1,
          CASE WHEN (gm1::int)=6 AND (gm2::int)=6 AND game_score ~ '^[0-9]+-[0-9]+$' THEN
               CASE WHEN player_won=2 THEN (split_part(game_score, '-', 2)::int)+1 ELSE (split_part(game_score, '-', 2)::int) END
          ELSE null END as t2
        FROM set_ends
        WHERE rn = 1
      )
      SELECT * FROM set_scores ORDER BY match_id, curr_set LIMIT 10;
  `, ['Carlos_Alcaraz', 'Daniil_Medvedev']);
    console.log(result.rows);
    process.exit(0);
}

run().catch(console.error);
