const express = require('express');
const { Pool } = require('pg');
const cors = require('cors');
const path = require('path');

const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static('docs'));

const pool = new Pool({
  user: 'postgres',
  password: '139071',
  host: 'localhost',
  port: 5432,
  database: 'tennis',
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
      ${POINT_WIN_LOGIC_CTE}
      SELECT
        serve.serve_direction::text AS serve_dir,
        next_shot.shot_type::text AS response_type,
        COUNT(*) AS total,
        SUM(pw.player_won) AS points_won
      FROM shot serve
      JOIN shot next_shot ON serve.point_number = next_shot.point_number AND serve.point_match_id = next_shot.point_match_id AND next_shot.number = 1
      JOIN point p ON serve.point_number = p.number AND serve.point_match_id = p.match_id
      JOIN match m ON p.match_id = m.id
      JOIN match_players mp ON p.match_id = mp.match_id
      JOIN point_winners pw ON pw.point_number = serve.point_number AND pw.point_match_id = serve.point_match_id
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
                 ELSE shot_type::text || '_' || direction::text END
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

const PORT = 3001;
app.listen(PORT, () => {
  console.log(`Tennis Analytics API running on http://localhost:${PORT}`);
});
