require('dotenv').config();
const express = require('express');
const { Pool } = require('pg');
const cors = require('cors');
require('dotenv').config();

const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static('docs'));

const pool = new Pool({
  user: process.env.DB_USER,
  password: process.env.DB_PASSWORD,
  host: process.env.DB_HOST,
  port: process.env.DB_PORT,
  database: process.env.DB_NAME,
});

function wilsonInterval(wins, total, z = 1.96) {
  if (total === 0) return { lower: 0, upper: 0, center: 0 };
  const p = wins / total;
  const denom = 1 + (z * z) / total;
  const center = (p + (z * z) / (2 * total)) / denom;
  const margin = (z * Math.sqrt((p * (1 - p)) / total + (z * z) / (4 * total * total))) / denom;
  return { lower: Math.max(0, center - margin), upper: Math.min(1, center + margin), center };
}

function buildFilterClause(query, params, paramIndex) {
  let clauses = [];
  if (query.surface) {
    clauses.push(`m.surface = $${paramIndex++}`);
    params.push(query.surface);
  }
  if (query.dateFrom) {
    clauses.push(`m.date >= $${paramIndex++}::date`);
    params.push(query.dateFrom);
  }
  if (query.dateTo) {
    clauses.push(`m.date <= $${paramIndex++}::date`);
    params.push(query.dateTo);
  }
  if (query.side) {
    if (query.side === 'Deuce') clauses.push(`(p.game_score IN ('0-0','15-15','30-30','40-40','15-30','30-15','0-15','15-0','0-30','30-0','40-15','15-40','30-40','40-30'))`);
    else if (query.side === 'Ad') clauses.push(`(p.game_score IN ('40-0','0-40','40-15','15-40','30-40','40-30','40-AD','AD-40'))`);
  }
  return { clauses, params, paramIndex };
}

// ─── Point Win Logic Helpers ─────────────────────────────────────────

async function getServeBaselineWinRate(playerName, queryObj) {
  let params = [playerName];
  let filterResult = buildFilterClause(queryObj, params, 2);
  params = filterResult.params;
  let filterSQL = filterResult.clauses.length > 0 ? 'AND ' + filterResult.clauses.join(' AND ') : '';

  const query = `
    WITH match_players AS (
      SELECT m.id AS match_id, CASE WHEN m.first_player_name = $1 THEN 1 ELSE 2 END AS player_num
      FROM match m WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
    )
    SELECT
      COUNT(*) AS total_points,
      SUM(CASE WHEN p.player_won = mp.player_num THEN 1 ELSE 0 END) AS won_points
    FROM point p
    JOIN match_players mp ON p.match_id = mp.match_id
    JOIN match m ON m.id = p.match_id
    WHERE 1=1 
    AND ((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0))
    ${filterSQL}
  `;
  const res = await pool.query(query, params);
  const total = parseInt(res.rows[0]?.total_points || 0);
  const won = parseInt(res.rows[0]?.won_points || 0);
  return total > 0 ? won / total : 0;
}

async function getBaselineWinRate(playerName, queryObj) {
  let params = [playerName];
  let filterResult = buildFilterClause(queryObj, params, 2);
  params = filterResult.params;
  let filterSQL = filterResult.clauses.length > 0 ? 'AND ' + filterResult.clauses.join(' AND ') : '';

  const query = `
    WITH match_players AS (
      SELECT m.id AS match_id, CASE WHEN m.first_player_name = $1 THEN 1 ELSE 2 END AS player_num
      FROM match m WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
    )
    SELECT
      COUNT(*) AS total_points,
      SUM(CASE WHEN p.player_won = mp.player_num THEN 1 ELSE 0 END) AS won_points
    FROM point p
    JOIN match_players mp ON p.match_id = mp.match_id
    JOIN match m ON m.id = p.match_id
    WHERE 1=1 ${filterSQL}
  `;
  const res = await pool.query(query, params);
  const total = parseInt(res.rows[0]?.total_points || 0);
  const won = parseInt(res.rows[0]?.won_points || 0);
  return total > 0 ? won / total : 0;
}

// ─── API Endpoints ───────────────────────────────────────────────────

app.get('/api/players', async (req, res) => {
  try {
    const q = req.query.q || '';
    const isAll = req.query.all === 'true';
    const limitClause = isAll ? '' : 'LIMIT 20';
    const result = await pool.query(`
      SELECT DISTINCT name FROM (
        SELECT first_player_name AS name FROM match
        UNION
        SELECT second_player_name AS name FROM match
      ) all_players
      WHERE LOWER(name) LIKE LOWER($1)
      ORDER BY name ${limitClause}
    `, [`%${q}%`]);
    res.json(result.rows.map(r => r.name));
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/api/player/:name/coverage', async (req, res) => {
  try {
    const playerName = req.params.name;
    const result = await pool.query(`
      SELECT EXTRACT(YEAR FROM m.date)::int AS year, COALESCE(m.surface, 'Unknown') AS surface,
        COUNT(DISTINCT m.id) AS matches, COUNT(DISTINCT p.number || '-' || p.match_id::text) AS points
      FROM match m JOIN point p ON p.match_id = m.id
      WHERE m.first_player_name = $1 OR m.second_player_name = $1
      GROUP BY EXTRACT(YEAR FROM m.date), m.surface ORDER BY year DESC, surface
    `, [playerName]);
    const totals = await pool.query(`
      SELECT COUNT(DISTINCT m.id) AS total_matches, COUNT(*) AS total_points
      FROM match m JOIN point p ON p.match_id = m.id
      WHERE m.first_player_name = $1 OR m.second_player_name = $1
    `, [playerName]);
    res.json({ breakdown: result.rows, totals: totals.rows[0] });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/api/player/:name/patterns', async (req, res) => {
  try {
    const playerName = req.params.name;
    const pwBase = await getBaselineWinRate(playerName, req.query);
    const minN = parseInt(req.query.minN) || 15;
    let params = [playerName];
    let filterResult = buildFilterClause(req.query, params, 2);
    params = filterResult.params;
    let filterSQL = filterResult.clauses.length > 0 ? 'AND ' + filterResult.clauses.join(' AND ') : '';

    const result = await pool.query(`
      WITH match_players AS (
        SELECT m.id AS match_id, CASE WHEN m.first_player_name = $1 THEN 1 ELSE 2 END AS player_num
        FROM match m WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
      ),
      player_shots AS (
        SELECT s.shot_type::text AS shot_type, CASE WHEN p.player_won = mp.player_num THEN 1 ELSE 0 END AS player_won
        FROM shot s
        JOIN point p ON s.point_number = p.number AND s.point_match_id = p.match_id
        JOIN match m ON p.match_id = m.id
        JOIN match_players mp ON p.match_id = mp.match_id
        WHERE s.shot_type != 'UNKNOWN_SHOT_TYPE'
        AND (
          (mp.player_num = 1 AND ( 
              (((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0)) AND s.number % 2 = 0) OR
              (NOT ((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0)) AND s.number % 2 = 1)
          ))
          OR
          (mp.player_num = 2 AND (
              (((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0)) AND s.number % 2 = 0) OR
              (NOT ((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0)) AND s.number % 2 = 1)
          ))
        )
        ${filterSQL}
      )
      SELECT shot_type, COUNT(*) AS total, SUM(player_won) AS points_won
      FROM player_shots GROUP BY shot_type ORDER BY total DESC
    `, params);

    const patterns = result.rows.map(row => {
      const total = parseInt(row.total);
      const won = parseInt(row.points_won);
      const winnerRate = total > 0 ? won / total : 0;
      return {
        shotType: row.shot_type,
        total,
        winnerRate,
        adjustedEffectiveness: total > 0 ? winnerRate - pwBase : 0,
        confidence: total >= 30 ? 'high' : total >= minN ? 'low' : 'insufficient',
      };
    }).filter(p => p.total >= minN);

    res.json(patterns);
  } catch (err) { console.error(err); res.status(500).json({ error: err.message }); }
});

app.get('/api/player/:name/serve', async (req, res) => {
  try {
    const playerName = req.params.name;
    let params = [playerName];
    let filterResult = buildFilterClause(req.query, params, 2);
    params = filterResult.params;
    let filterSQL = filterResult.clauses.length > 0 ? 'AND ' + filterResult.clauses.join(' AND ') : '';

    const result = await pool.query(`
      WITH match_players AS (
        SELECT m.id AS match_id, CASE WHEN m.first_player_name = $1 THEN 1 ELSE 2 END AS player_num
        FROM match m WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
      ),
      player_serves AS (
        SELECT s.serve_direction::text AS direction, CASE WHEN p.player_won = mp.player_num THEN 1 ELSE 0 END AS player_won, s.outcome
        FROM shot s
        JOIN point p ON s.point_number = p.number AND s.point_match_id = p.match_id
        JOIN match m ON p.match_id = m.id
        JOIN match_players mp ON p.match_id = mp.match_id
        WHERE s.number = 0 AND s.serve_direction != 'UNKNOWN_SERVE_DIRECTION'
        AND ((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0))
        ${filterSQL}
      )
      SELECT direction, COUNT(*) AS total, SUM(player_won) AS points_won,
             COUNT(*) FILTER (WHERE outcome IN ('UNFORCED_ERROR', 'FORCED_ERROR')) AS double_faults
      FROM player_serves GROUP BY direction ORDER BY total DESC
    `, params);

    res.json(result.rows.map(r => {
      const total = parseInt(r.total);
      return {
        direction: r.direction,
        total,
        winnerRate: total > 0 ? parseInt(r.points_won) / total : 0,
        errorRate: total > 0 ? parseInt(r.double_faults) / total : 0,
        confidence: total >= 30 ? 'high' : 'low'
      };
    }));
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/api/player/:name/serve-plus-one', async (req, res) => {
  try {
    const playerName = req.params.name;
    const pwBase = await getServeBaselineWinRate(playerName, req.query);
    const minN = parseInt(req.query.minN) || 15;
    let params = [playerName];
    let filterResult = buildFilterClause(req.query, params, 2);
    params = filterResult.params;
    let filterSQL = filterResult.clauses.length > 0 ? 'AND ' + filterResult.clauses.join(' AND ') : '';

    const result = await pool.query(`
      WITH match_players AS (
        SELECT m.id AS match_id, CASE WHEN m.first_player_name = $1 THEN 1 ELSE 2 END AS player_num
        FROM match m WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
      )
      SELECT
        serve.serve_direction::text AS serve_dir,
        next_shot.shot_type::text AS response_type,
        COUNT(*) AS total,
        SUM(CASE WHEN p.player_won = mp.player_num THEN 1 ELSE 0 END) AS points_won
      FROM shot serve
      JOIN shot next_shot ON serve.point_number = next_shot.point_number AND serve.point_match_id = next_shot.point_match_id AND next_shot.number = 1
      JOIN point p ON serve.point_number = p.number AND serve.point_match_id = p.match_id
      JOIN match m ON p.match_id = m.id
      JOIN match_players mp ON p.match_id = mp.match_id
      WHERE serve.number = 0 AND serve.serve_direction != 'UNKNOWN_SERVE_DIRECTION'
        AND ((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0))
        ${filterSQL}
      GROUP BY serve.serve_direction, next_shot.shot_type
    `, params);

    res.json(result.rows.map(r => {
      const total = parseInt(r.total);
      const winnerRate = total > 0 ? parseInt(r.points_won) / total : 0;
      return {
        serveDir: r.serve_dir,
        responseType: r.response_type,
        total,
        winnerRate,
        adjustedEffectiveness: total > 0 ? winnerRate - pwBase : 0,
      };
    }).filter(x => x.total >= minN));
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/api/player/:name/direction-patterns', async (req, res) => {
  try {
    const playerName = req.params.name;
    const pwBase = await getBaselineWinRate(playerName, req.query);
    const minN = parseInt(req.query.minN) || 15;
    let params = [playerName];
    let filterResult = buildFilterClause(req.query, params, 2);
    params = filterResult.params;
    let filterSQL = filterResult.clauses.length > 0 ? 'AND ' + filterResult.clauses.join(' AND ') : '';

    const result = await pool.query(`
      WITH match_players AS (
        SELECT m.id AS match_id, CASE WHEN m.first_player_name = $1 THEN 1 ELSE 2 END AS player_num
        FROM match m WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
      ),
      player_shots AS (
        SELECT s.shot_type::text AS shot_type, s.direction::text AS direction, CASE WHEN p.player_won = mp.player_num THEN 1 ELSE 0 END AS player_won
        FROM shot s
        JOIN point p ON s.point_number = p.number AND s.point_match_id = p.match_id
        JOIN match m ON p.match_id = m.id
        JOIN match_players mp ON p.match_id = mp.match_id
        WHERE s.shot_type != 'UNKNOWN_SHOT_TYPE' AND s.direction != 'UNKNOWN_DIRECTION'
        AND (
          (mp.player_num = 1 AND ( 
              (((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0)) AND s.number % 2 = 0) OR
              (NOT ((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0)) AND s.number % 2 = 1)
          ))
          OR
          (mp.player_num = 2 AND (
              (((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0)) AND s.number % 2 = 0) OR
              (NOT ((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0)) AND s.number % 2 = 1)
          ))
        )
        ${filterSQL}
      )
      SELECT shot_type, direction, COUNT(*) AS total, SUM(player_won) AS points_won
      FROM player_shots GROUP BY shot_type, direction ORDER BY total DESC
    `, params);

    res.json(result.rows.map(r => {
      const total = parseInt(r.total);
      const winnerRate = total > 0 ? parseInt(r.points_won) / total : 0;
      return {
        shotType: r.shot_type,
        direction: r.direction,
        total,
        winnerRate,
        adjustedEffectiveness: total > 0 ? winnerRate - pwBase : 0,
      };
    }).filter(x => x.total >= minN));
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/api/player/:name/compare', async (req, res) => {
  try {
    const playerName = req.params.name;
    const pwBase = await getBaselineWinRate(playerName, req.query);
    const minN = parseInt(req.query.minN) || 15;
    let params = [playerName];
    let filterResult = buildFilterClause(req.query, params, 2);
    params = filterResult.params;
    let filterSQL = filterResult.clauses.length > 0 ? 'AND ' + filterResult.clauses.join(' AND ') : '';

    const result = await pool.query(`
      WITH match_players AS (
        SELECT m.id AS match_id, CASE WHEN m.first_player_name = $1 THEN 1 ELSE 2 END AS player_num,
               CASE WHEN m.first_player_name = $1 THEN 'win' ELSE 'loss' END AS result
        FROM match m WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
      ),
      player_shots AS (
        SELECT s.shot_type::text AS shot_type, CASE WHEN p.player_won = mp.player_num THEN 1 ELSE 0 END AS player_won, mp.result
        FROM shot s
        JOIN point p ON s.point_number = p.number AND s.point_match_id = p.match_id
        JOIN match m ON p.match_id = m.id
        JOIN match_players mp ON p.match_id = mp.match_id
        WHERE s.shot_type != 'UNKNOWN_SHOT_TYPE'
        AND (
          (mp.player_num = 1 AND ( 
              (((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0)) AND s.number % 2 = 0) OR
              (NOT ((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0)) AND s.number % 2 = 1)
          ))
          OR
          (mp.player_num = 2 AND (
              (((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0)) AND s.number % 2 = 0) OR
              (NOT ((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0)) AND s.number % 2 = 1)
          ))
        )
        ${filterSQL}
      )
      SELECT result, shot_type, COUNT(*) AS total, SUM(player_won) AS points_won
      FROM player_shots GROUP BY result, shot_type
    `, params);

    const formatPatterns = (rows) => rows.map(r => {
      const total = parseInt(r.total);
      const winnerRate = total > 0 ? parseInt(r.points_won) / total : 0;
      return {
        shotType: r.shot_type,
        total,
        winnerRate,
        adjustedEffectiveness: total > 0 ? winnerRate - pwBase : 0,
      };
    }).filter(x => x.total >= minN);

    res.json({
      wins: formatPatterns(result.rows.filter(r => r.result === 'win')),
      losses: formatPatterns(result.rows.filter(r => r.result === 'loss'))
    });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/api/player/:name/insights', async (req, res) => {
  try {
    const playerName = req.params.name;
    // We will build insights from the /patterns endpoint logic
    const pwBase = await getBaselineWinRate(playerName, req.query);
    const minN = parseInt(req.query.minN) || 15;
    let params = [playerName];
    let filterResult = buildFilterClause(req.query, params, 2);
    params = filterResult.params;
    let filterSQL = filterResult.clauses.length > 0 ? 'AND ' + filterResult.clauses.join(' AND ') : '';

    const result = await pool.query(`
      WITH match_players AS (
        SELECT m.id AS match_id, CASE WHEN m.first_player_name = $1 THEN 1 ELSE 2 END AS player_num
        FROM match m WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
      ),
      player_shots AS (
        SELECT s.shot_type::text AS shot_type, s.direction::text AS direction, CASE WHEN p.player_won = mp.player_num THEN 1 ELSE 0 END AS player_won
        FROM shot s
        JOIN point p ON s.point_number = p.number AND s.point_match_id = p.match_id
        JOIN match m ON p.match_id = m.id
        JOIN match_players mp ON p.match_id = mp.match_id
        WHERE s.shot_type != 'UNKNOWN_SHOT_TYPE'
        AND (
          (mp.player_num = 1 AND ( 
              (((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0)) AND s.number % 2 = 0) OR
              (NOT ((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0)) AND s.number % 2 = 1)
          ))
          OR
          (mp.player_num = 2 AND (
              (((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0)) AND s.number % 2 = 0) OR
              (NOT ((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0)) AND s.number % 2 = 1)
          ))
        )
        ${filterSQL}
      )
      SELECT shot_type, COUNT(*) AS total, SUM(player_won) AS points_won
      FROM player_shots GROUP BY shot_type
    `, params);

    const patterns = result.rows.map(row => {
      const total = parseInt(row.total);
      if (total < minN) return null;
      const winnerRate = parseInt(row.points_won) / total;
      return {
        shotType: row.shot_type,
        total,
        uplift: winnerRate - pwBase,
      };
    }).filter(p => p !== null);

    patterns.sort((a, b) => Math.abs(b.uplift) - Math.abs(a.uplift));
    const top3 = patterns.slice(0, 3).map(p => {
      const shotLabel = p.shotType.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase());
      const isStrength = p.uplift > 0;
      return {
        type: isStrength ? 'strength' : 'weakness',
        icon: isStrength ? '🟢' : '🔴',
        title: `${isStrength ? 'Strength' : 'Weakness'}: ${shotLabel}`,
        detail: `When using this shot, point win rate is ${(p.uplift * 100 > 0 ? '+' : '')}${(p.uplift * 100).toFixed(1)}% vs baseline. Evidence: N=${p.total}`,
      };
    });

    res.json(top3);
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── Phase 2 MVP Option A: Pattern Inference ─────────────────────────
app.get('/api/player/:name/pattern-inference', async (req, res) => {
  try {
    const playerName = req.params.name;
    const pwBase = await getBaselineWinRate(playerName, req.query);
    const minN = parseInt(req.query.minN) || 15;
    let params = [playerName];
    let filterResult = buildFilterClause(req.query, params, 2);
    params = filterResult.params;
    let filterSQL = filterResult.clauses.length > 0 ? 'AND ' + filterResult.clauses.join(' AND ') : '';

    const query = `
      WITH match_players AS (
        SELECT m.id AS match_id, CASE WHEN m.first_player_name = $1 THEN 1 ELSE 2 END AS player_num
        FROM match m WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
      ),
      point_shots AS (
        SELECT 
          s.point_match_id, s.point_number, s.number AS shot_num, s.shot_type, s.direction, s.serve_direction,
          s.depth, s.outcome,
          CASE WHEN p.player_won = mp.player_num THEN 1 ELSE 0 END AS player_won
        FROM shot s
        JOIN point p ON s.point_number = p.number AND s.point_match_id = p.match_id
        JOIN match m ON p.match_id = m.id
        JOIN match_players mp ON p.match_id = mp.match_id
        WHERE 1=1 ${filterSQL}
        ORDER BY s.point_match_id, s.point_number, s.number
      )
      SELECT point_match_id, point_number, player_won,
             array_agg(
                 CASE WHEN shot_num = 0 THEN serve_direction::text 
                 ELSE shot_type::text || '_' || direction::text ||
                      CASE WHEN depth IS NOT NULL AND depth != 'UNKNOWN_DEPTH' THEN '_' || depth::text ELSE '' END ||
                      CASE WHEN outcome IN ('WINNER', 'UNFORCED_ERROR', 'FORCED_ERROR') THEN '_' || outcome::text ELSE '' END
                 END
                 ORDER BY shot_num
             ) AS seq
      FROM point_shots
      GROUP BY point_match_id, point_number, player_won
    `;
    const result = await pool.query(query, params);

    const nGrams = {};
    for (const point of result.rows) {
      const seq = point.seq.filter(s => s && s.indexOf('UNKNOWN') === -1);
      const won = point.player_won;

      for (let n = 2; n <= 4; n++) { // extract 2 to 4 length n-grams
        for (let i = 0; i <= seq.length - n; i++) {
          const slice = seq.slice(i, i + n);
          const key = slice.join('|');
          if (!nGrams[key]) nGrams[key] = { sequence: slice, total: 0, won: 0 };
          nGrams[key].total++;
          nGrams[key].won += won;
        }
      }
    }

    const scored = Object.values(nGrams).map(g => {
      const winRate = g.total > 0 ? g.won / g.total : 0;
      const uplift = winRate - pwBase;
      const rankScore = uplift * Math.log(g.total);
      return {
        sequence: g.sequence,
        total: g.total,
        winnerRate: winRate,
        uplift,
        rankScore
      };
    }).filter(g => g.total >= minN);

    scored.sort((a, b) => b.rankScore - a.rankScore);
    const winning = scored.filter(s => s.uplift > 0).slice(0, 15);
    const losing = scored.filter(s => s.uplift < 0).sort((a, b) => a.rankScore - b.rankScore).slice(0, 15);

    res.json({ winning, losing });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── Serve Detailed: 1st/2nd Serve Split ──────────────────────────────
app.get('/api/player/:name/serve-detailed', async (req, res) => {
  try {
    const playerName = req.params.name;
    let params = [playerName];
    let filterResult = buildFilterClause(req.query, params, 2);
    params = filterResult.params;
    let filterSQL = filterResult.clauses.length > 0 ? 'AND ' + filterResult.clauses.join(' AND ') : '';

    // Identify which points had a 2nd serve (i.e., the point has 2+ serve shots at number 0 and 1)
    // In Zifan's data model: if 2nd column was populated, there are two serve shots for that point
    // shot.number=0 is always a serve. If there's also a shot where the serve happened on number=1 (after 1st fault),
    // we detect this by counting serve-type shots per point
    const result = await pool.query(`
      WITH match_players AS (
        SELECT m.id AS match_id, CASE WHEN m.first_player_name = $1 THEN 1 ELSE 2 END AS player_num
        FROM match m WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
      ),
      serve_points AS (
        SELECT
          p.match_id, p.number AS point_num,
          mp.player_num,
          CASE WHEN p.player_won = mp.player_num THEN 1 ELSE 0 END AS point_won,
          COUNT(CASE WHEN s.serve_direction != 'UNKNOWN_SERVE_DIRECTION' THEN 1 END) AS serve_count,
          MAX(CASE WHEN s.number = 0 THEN s.serve_direction::text END) AS first_serve_dir,
          MAX(CASE WHEN s.number = 0 AND s.outcome IN ('WINNER') THEN 1 ELSE 0 END) AS is_ace
        FROM point p
        JOIN match_players mp ON p.match_id = mp.match_id
        JOIN match m ON m.id = p.match_id
        LEFT JOIN shot s ON s.point_number = p.number AND s.point_match_id = p.match_id
        WHERE ((mp.player_num = 1 AND p.number % 2 = 1) OR (mp.player_num = 2 AND p.number % 2 = 0))
        ${filterSQL}
        GROUP BY p.match_id, p.number, mp.player_num, p.player_won
      )
      SELECT
        -- Overall
        COUNT(*) AS total_serve_points,
        SUM(point_won) AS total_points_won,
        -- 1st serve (points where only 1 serve was needed)
        COUNT(*) FILTER (WHERE serve_count <= 1) AS first_serve_in,
        SUM(point_won) FILTER (WHERE serve_count <= 1) AS first_serve_won,
        -- 2nd serve (points where 2 serves happened)
        COUNT(*) FILTER (WHERE serve_count > 1) AS second_serve_total,
        SUM(point_won) FILTER (WHERE serve_count > 1) AS second_serve_won,
        -- Aces (on 1st serve mostly)
        SUM(is_ace) AS aces,
        -- Double faults = 2nd serve points where point was lost immediately
        COUNT(*) FILTER (WHERE serve_count > 1 AND point_won = 0) AS double_fault_candidates
      FROM serve_points
    `, params);

    const r = result.rows[0];
    const totalServe = parseInt(r.total_serve_points) || 0;
    const firstIn = parseInt(r.first_serve_in) || 0;
    const firstWon = parseInt(r.first_serve_won) || 0;
    const secondTotal = parseInt(r.second_serve_total) || 0;
    const secondWon = parseInt(r.second_serve_won) || 0;
    const aces = parseInt(r.aces) || 0;

    res.json({
      totalServePoints: totalServe,
      firstServeIn: firstIn,
      firstServeInPct: totalServe > 0 ? firstIn / totalServe : 0,
      firstServeWon: firstWon,
      firstServeWinPct: firstIn > 0 ? firstWon / firstIn : 0,
      secondServeTotal: secondTotal,
      secondServeWon: secondWon,
      secondServeWinPct: secondTotal > 0 ? secondWon / secondTotal : 0,
      aces,
      acePct: totalServe > 0 ? aces / totalServe : 0,
      doubleFaults: secondTotal - secondWon, // approximate
      doubleFaultPct: totalServe > 0 ? (secondTotal - secondWon) / totalServe : 0,
    });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── Head-to-Head ─────────────────────────────────────────────────────
app.get('/api/h2h/:playerA/:playerB', async (req, res) => {
  try {
    const { playerA, playerB } = req.params;
    const result = await pool.query(`
      SELECT
        m.id, m.date, m.first_player_name, m.second_player_name,
        COALESCE(m.surface, 'Unknown') AS surface,
        m.event_round,
        e.name AS tournament
      FROM match m
      LEFT JOIN event e ON m.event_id = e.id
      WHERE (m.first_player_name = $1 AND m.second_player_name = $2)
         OR (m.first_player_name = $2 AND m.second_player_name = $1)
      ORDER BY m.date DESC
    `, [playerA, playerB]);

    let winsA = 0, winsB = 0;
    const matches = result.rows.map(r => {
      const winner = r.first_player_name; // first_player_name is always winner in this schema
      if (winner === playerA) winsA++;
      else winsB++;
      return {
        date: r.date,
        surface: r.surface,
        tournament: r.tournament || 'Unknown',
        round: r.event_round || '',
        winner
      };
    });

    res.json({
      playerA, playerB,
      winsA, winsB,
      totalMatches: matches.length,
      mostRecent: matches.length > 0 ? matches[0] : null,
      bySurface: {
        Hard: { winsA: matches.filter(m => m.surface === 'Hard' && m.winner === playerA).length, winsB: matches.filter(m => m.surface === 'Hard' && m.winner === playerB).length },
        Clay: { winsA: matches.filter(m => m.surface === 'Clay' && m.winner === playerA).length, winsB: matches.filter(m => m.surface === 'Clay' && m.winner === playerB).length },
        Grass: { winsA: matches.filter(m => m.surface === 'Grass' && m.winner === playerA).length, winsB: matches.filter(m => m.surface === 'Grass' && m.winner === playerB).length },
      }
    });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── Rally Comfort Zone ───────────────────────────────────────────────
app.get('/api/player/:name/rally-comfort', async (req, res) => {
  try {
    const playerName = req.params.name;
    let params = [playerName];
    let filterResult = buildFilterClause(req.query, params, 2);
    params = filterResult.params;
    let filterSQL = filterResult.clauses.length > 0 ? 'AND ' + filterResult.clauses.join(' AND ') : '';

    const result = await pool.query(`
      WITH match_players AS (
        SELECT m.id AS match_id, CASE WHEN m.first_player_name = $1 THEN 1 ELSE 2 END AS player_num
        FROM match m WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
      ),
      rally_points AS (
        SELECT
          p.match_id, p.number, p.rally_length,
          CASE WHEN p.player_won = mp.player_num THEN 1 ELSE 0 END AS point_won,
          CASE
            WHEN p.rally_length BETWEEN 1 AND 3 THEN '1-3'
            WHEN p.rally_length BETWEEN 4 AND 6 THEN '4-6'
            WHEN p.rally_length BETWEEN 7 AND 9 THEN '7-9'
            WHEN p.rally_length BETWEEN 10 AND 12 THEN '10-12'
            WHEN p.rally_length >= 13 THEN '13+'
          END AS bucket
        FROM point p
        JOIN match_players mp ON p.match_id = mp.match_id
        JOIN match m ON m.id = p.match_id
        WHERE p.rally_length IS NOT NULL
        ${filterSQL}
      )
      SELECT bucket, COUNT(*) AS n, SUM(point_won) AS wins
      FROM rally_points
      WHERE bucket IS NOT NULL
      GROUP BY bucket
      HAVING COUNT(*) >= 20
      ORDER BY MIN(rally_length)
    `, params);

    const buckets = result.rows.map(r => ({
      range: r.bucket,
      n: parseInt(r.n),
      pt_win_rate: parseInt(r.n) > 0 ? parseInt(r.wins) / parseInt(r.n) : 0,
    }));

    // Find peak bucket
    let peakBucket = null;
    let peakRate = 0;
    for (const b of buckets) {
      if (b.pt_win_rate > peakRate) {
        peakRate = b.pt_win_rate;
        peakBucket = b.range;
      }
    }

    // Get top patterns in peak bucket
    let peakPatterns = [];
    if (peakBucket) {
      const [lo, hi] = peakBucket === '13+' ? [13, 999] : peakBucket.split('-').map(Number);
      const patResult = await pool.query(`
        WITH match_players AS (
          SELECT m.id AS match_id, CASE WHEN m.first_player_name = $1 THEN 1 ELSE 2 END AS player_num
          FROM match m WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
        )
        SELECT s.shot_type, COUNT(*) AS n,
               SUM(CASE WHEN p.player_won = mp.player_num THEN 1 ELSE 0 END) AS wins
        FROM shot s
        JOIN point p ON s.point_match_id = p.match_id AND s.point_number = p.number
        JOIN match_players mp ON p.match_id = mp.match_id
        WHERE p.rally_length BETWEEN $2 AND $3
          AND s.shot_type IS NOT NULL
          AND s.shot_type != 'UNKNOWN_SHOT_TYPE'
        GROUP BY s.shot_type
        HAVING COUNT(*) >= 10
        ORDER BY (SUM(CASE WHEN p.player_won = mp.player_num THEN 1 ELSE 0 END)::FLOAT / COUNT(*)) DESC
        LIMIT 5
      `, [playerName, lo, hi]);

      peakPatterns = patResult.rows.map(r => ({
        pattern: r.shot_type,
        n: parseInt(r.n),
        pt_win_rate: parseInt(r.n) > 0 ? parseInt(r.wins) / parseInt(r.n) : 0,
      }));
    }

    res.json({ buckets, peak_bucket: peakBucket, peak_patterns: peakPatterns });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── Pressure-State Tendencies ────────────────────────────────────────
app.get('/api/player/:name/pressure-state', async (req, res) => {
  try {
    const playerName = req.params.name;
    let params = [playerName];
    let filterResult = buildFilterClause(req.query, params, 2);
    params = filterResult.params;
    let filterSQL = filterResult.clauses.length > 0 ? 'AND ' + filterResult.clauses.join(' AND ') : '';

    const result = await pool.query(`
      WITH match_players AS (
        SELECT m.id AS match_id, CASE WHEN m.first_player_name = $1 THEN 1 ELSE 2 END AS player_num
        FROM match m WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
      )
      SELECT
        COALESCE(p.score_state_label, 'neutral') AS state_label,
        COUNT(*) AS n,
        SUM(CASE WHEN p.player_won = mp.player_num THEN 1 ELSE 0 END) AS wins
      FROM point p
      JOIN match_players mp ON p.match_id = mp.match_id
      JOIN match m ON m.id = p.match_id
      WHERE p.score_state_label IS NOT NULL
      ${filterSQL}
      GROUP BY p.score_state_label
      HAVING COUNT(*) >= 20
      ORDER BY state_label
    `, params);

    // Find baseline (neutral state)
    const neutralRow = result.rows.find(r => r.state_label === 'neutral');
    const baselineWinRate = neutralRow ? parseInt(neutralRow.wins) / parseInt(neutralRow.n) : 0.5;

    const states = result.rows.map(r => {
      const n = parseInt(r.n);
      const wins = parseInt(r.wins);
      return {
        label: r.state_label,
        n,
        pt_win_rate: n > 0 ? wins / n : 0,
      };
    });

    res.json({
      baseline: { pt_win_rate: baselineWinRate },
      states
    });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── Opponent-Specific Pattern Shifts ─────────────────────────────────
app.get('/api/player/:name/opponent-shifts', async (req, res) => {
  try {
    const playerName = req.params.name;

    // Segment by opponent hand
    const handResult = await pool.query(`
      WITH match_context AS (
        SELECT m.id, m.first_player_name, m.second_player_name,
          CASE WHEN m.first_player_name = $1 THEN m.p2_hand ELSE m.p1_hand END AS opp_hand,
          CASE WHEN m.first_player_name = $1 THEN 1 ELSE 2 END AS player_num
        FROM match m
        WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
      )
      SELECT mc.opp_hand, COUNT(DISTINCT mc.id) AS matches,
        COUNT(p.number) AS points,
        SUM(CASE WHEN p.player_won = mc.player_num THEN 1 ELSE 0 END) AS wins
      FROM match_context mc
      JOIN point p ON p.match_id = mc.id
      WHERE mc.opp_hand IS NOT NULL
      GROUP BY mc.opp_hand
      HAVING COUNT(DISTINCT mc.id) >= 3 AND COUNT(p.number) >= 20
    `, [playerName]);

    // Segment by opponent rank tier
    const rankResult = await pool.query(`
      WITH match_context AS (
        SELECT m.id, m.first_player_name, m.second_player_name,
          m.opponent_rank_tier,
          CASE WHEN m.first_player_name = $1 THEN 1 ELSE 2 END AS player_num
        FROM match m
        WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
      )
      SELECT mc.opponent_rank_tier AS tier, COUNT(DISTINCT mc.id) AS matches,
        COUNT(p.number) AS points,
        SUM(CASE WHEN p.player_won = mc.player_num THEN 1 ELSE 0 END) AS wins
      FROM match_context mc
      JOIN point p ON p.match_id = mc.id
      WHERE mc.opponent_rank_tier IS NOT NULL
      GROUP BY mc.opponent_rank_tier
      HAVING COUNT(DISTINCT mc.id) >= 3 AND COUNT(p.number) >= 20
    `, [playerName]);

    // Overall baseline
    const baseResult = await pool.query(`
      WITH match_players AS (
        SELECT m.id AS match_id, CASE WHEN m.first_player_name = $1 THEN 1 ELSE 2 END AS player_num
        FROM match m WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
      )
      SELECT COUNT(*) AS n, SUM(CASE WHEN p.player_won = mp.player_num THEN 1 ELSE 0 END) AS wins
      FROM point p JOIN match_players mp ON p.match_id = mp.match_id
    `, [playerName]);
    const baseN = parseInt(baseResult.rows[0].n) || 1;
    const baseWinRate = parseInt(baseResult.rows[0].wins) / baseN;

    const segments = [];

    for (const r of handResult.rows) {
      const winRate = parseInt(r.wins) / parseInt(r.points);
      const delta = winRate - baseWinRate;
      segments.push({
        segment_label: `vs ${r.opp_hand === 'L' ? 'Left-Handers' : 'Right-Handers'}`,
        matches: parseInt(r.matches),
        points: parseInt(r.points),
        pt_win_rate: winRate,
        delta,
        status: delta > 0.05 ? 'amplified' : delta < -0.05 ? 'weakened' : 'holds'
      });
    }

    for (const r of rankResult.rows) {
      const winRate = parseInt(r.wins) / parseInt(r.points);
      const delta = winRate - baseWinRate;
      segments.push({
        segment_label: `vs ${r.tier}`,
        matches: parseInt(r.matches),
        points: parseInt(r.points),
        pt_win_rate: winRate,
        delta,
        status: delta > 0.05 ? 'amplified' : delta < -0.05 ? 'weakened' : 'holds'
      });
    }

    res.json({ baseline_win_rate: baseWinRate, segments });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

const PORT = 3001;
app.listen(PORT, () => {
  console.log(`Tennis Analytics API running on http://localhost:${PORT}`);
});
