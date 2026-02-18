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

// ─── Wilson Score Interval ───────────────────────────────────────────
function wilsonInterval(wins, total, z = 1.96) {
  if (total === 0) return { lower: 0, upper: 0, center: 0 };
  const p = wins / total;
  const denom = 1 + (z * z) / total;
  const center = (p + (z * z) / (2 * total)) / denom;
  const margin = (z * Math.sqrt((p * (1 - p)) / total + (z * z) / (4 * total * total))) / denom;
  return {
    lower: Math.max(0, center - margin),
    upper: Math.min(1, center + margin),
    center: center,
  };
}

// ─── Helper: build WHERE clause from filters ─────────────────────────
function buildFilterClause(query, params, paramIndex, playerAlias = 'player_name') {
  let clauses = [];

  if (query.surface) {
    clauses.push(`m.surface = $${paramIndex}`);
    params.push(query.surface);
    paramIndex++;
  }
  if (query.dateFrom) {
    clauses.push(`m.date >= $${paramIndex}::date`);
    params.push(query.dateFrom);
    paramIndex++;
  }
  if (query.dateTo) {
    clauses.push(`m.date <= $${paramIndex}::date`);
    params.push(query.dateTo);
    paramIndex++;
  }
  if (query.side) {
    // Deuce = even points in game, Ad = odd
    if (query.side === 'Deuce') {
      clauses.push(`(p.game_score IN ('0-0','15-15','30-30','40-40','15-30','30-15','0-15','15-0','0-30','30-0','40-15','15-40','30-40','40-30'))`);
    } else if (query.side === 'Ad') {
      clauses.push(`(p.game_score IN ('40-0','0-40','40-15','15-40','30-40','40-30','40-AD','AD-40'))`);
    }
  }

  return { clauses, params, paramIndex };
}

// ─── Player Search Autocomplete ──────────────────────────────────────
app.get('/api/players', async (req, res) => {
  try {
    const q = req.query.q || '';
    const result = await pool.query(`
      SELECT DISTINCT name FROM (
        SELECT first_player_name AS name FROM match
        UNION
        SELECT second_player_name AS name FROM match
      ) all_players
      WHERE LOWER(name) LIKE LOWER($1)
      ORDER BY name
      LIMIT 20
    `, [`%${q}%`]);
    res.json(result.rows.map(r => r.name));
  } catch (err) {
    console.error('Player search error:', err);
    res.status(500).json({ error: err.message });
  }
});

// ─── Coverage Panel ──────────────────────────────────────────────────
app.get('/api/player/:name/coverage', async (req, res) => {
  try {
    const playerName = req.params.name;
    const result = await pool.query(`
      SELECT
        EXTRACT(YEAR FROM m.date)::int AS year,
        COALESCE(m.surface, 'Unknown') AS surface,
        COUNT(DISTINCT m.id) AS matches,
        COUNT(DISTINCT p.number || '-' || p.match_id::text) AS points
      FROM match m
      JOIN point p ON p.match_id = m.id
      WHERE m.first_player_name = $1 OR m.second_player_name = $1
      GROUP BY EXTRACT(YEAR FROM m.date), m.surface
      ORDER BY year DESC, surface
    `, [playerName]);

    // Also get totals
    const totals = await pool.query(`
      SELECT
        COUNT(DISTINCT m.id) AS total_matches,
        COUNT(*) AS total_points
      FROM match m
      JOIN point p ON p.match_id = m.id
      WHERE m.first_player_name = $1 OR m.second_player_name = $1
    `, [playerName]);

    res.json({
      breakdown: result.rows,
      totals: totals.rows[0],
    });
  } catch (err) {
    console.error('Coverage error:', err);
    res.status(500).json({ error: err.message });
  }
});

// ─── Shot Patterns with Wilson Intervals ─────────────────────────────
app.get('/api/player/:name/patterns', async (req, res) => {
  try {
    const playerName = req.params.name;
    const minN = parseInt(req.query.minN) || 30;
    let params = [playerName];
    let paramIdx = 2;
    let filterResult = buildFilterClause(req.query, params, paramIdx);
    params = filterResult.params;

    let filterSQL = filterResult.clauses.length > 0
      ? 'AND ' + filterResult.clauses.join(' AND ')
      : '';

    // Determine if player is player 1 or 2 to correctly attribute shots
    const result = await pool.query(`
      WITH player_points AS (
        SELECT
          p.number AS pt_num,
          p.match_id,
          p.game_score,
          CASE
            WHEN m.first_player_name = $1 THEN 1
            ELSE 2
          END AS player_num,
          CASE
            WHEN (m.first_player_name = $1 AND p.number % 2 = 1)
              OR (m.second_player_name = $1 AND p.number % 2 = 0)
            THEN true ELSE false
          END AS is_serving
        FROM match m
        JOIN point p ON p.match_id = m.id
        WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
        ${filterSQL}
      ),
      player_shots AS (
        SELECT
          s.shot_type::text AS shot_type,
          s.direction::text AS direction,
          s.depth::text AS depth,
          s.outcome::text AS outcome,
          s.number AS shot_num,
          pp.is_serving,
          pp.pt_num,
          pp.match_id,
          pp.player_num
        FROM player_points pp
        JOIN shot s ON s.point_number = pp.pt_num AND s.point_match_id = pp.match_id
        WHERE (
          (pp.player_num = 1 AND (
            (pp.is_serving = true AND s.number % 2 = 0) OR
            (pp.is_serving = false AND s.number % 2 = 1)
          )) OR
          (pp.player_num = 2 AND (
            (pp.is_serving = true AND s.number % 2 = 0) OR
            (pp.is_serving = false AND s.number % 2 = 1)
          ))
        )
      )
      SELECT
        shot_type,
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE outcome = 'WINNER') AS winners,
        COUNT(*) FILTER (WHERE outcome = 'UNFORCED_ERROR') AS unforced_errors,
        COUNT(*) FILTER (WHERE outcome = 'FORCED_ERROR') AS forced_errors,
        COUNT(*) FILTER (WHERE outcome = 'CONTINUE') AS continues
      FROM player_shots
      WHERE shot_type != 'UNKNOWN_SHOT_TYPE'
      GROUP BY shot_type
      ORDER BY total DESC
    `, params);

    const patterns = result.rows.map(row => {
      const total = parseInt(row.total);
      const winners = parseInt(row.winners);
      const ufe = parseInt(row.unforced_errors);
      const fe = parseInt(row.forced_errors);
      const errors = ufe + fe;
      const effectiveness = total > 0 ? (winners - errors) / total : 0;
      const winnerCI = wilsonInterval(winners, total);
      const errorCI = wilsonInterval(errors, total);

      return {
        shotType: row.shot_type,
        total,
        winners,
        unforcedErrors: ufe,
        forcedErrors: fe,
        effectiveness: Math.round(effectiveness * 1000) / 1000,
        winnerRate: total > 0 ? Math.round((winners / total) * 1000) / 1000 : 0,
        errorRate: total > 0 ? Math.round((errors / total) * 1000) / 1000 : 0,
        winnerCI,
        errorCI,
        confidence: total >= 30 ? 'high' : total >= 10 ? 'low' : 'insufficient',
      };
    });

    res.json(patterns.filter(p => p.total >= Math.min(minN, 10)));
  } catch (err) {
    console.error('Patterns error:', err);
    res.status(500).json({ error: err.message });
  }
});

// ─── Serve Analysis ──────────────────────────────────────────────────
app.get('/api/player/:name/serve', async (req, res) => {
  try {
    const playerName = req.params.name;
    let params = [playerName];
    let paramIdx = 2;
    let filterResult = buildFilterClause(req.query, params, paramIdx);
    params = filterResult.params;
    let filterSQL = filterResult.clauses.length > 0
      ? 'AND ' + filterResult.clauses.join(' AND ')
      : '';

    const result = await pool.query(`
      SELECT
        s.serve_direction::text AS serve_direction,
        s.outcome::text AS outcome,
        COUNT(*) AS total
      FROM shot s
      JOIN point p ON s.point_number = p.number AND s.point_match_id = p.match_id
      JOIN match m ON p.match_id = m.id
      WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
        AND s.number = 0
        AND s.serve_direction != 'UNKNOWN_SERVE_DIRECTION'
        ${filterSQL}
      GROUP BY s.serve_direction, s.outcome
      ORDER BY s.serve_direction, total DESC
    `, params);

    // Aggregate by direction
    const dirMap = {};
    for (const row of result.rows) {
      const dir = row.serve_direction;
      if (!dirMap[dir]) dirMap[dir] = { direction: dir, total: 0, ace: 0, winners: 0, errors: 0, inPlay: 0 };
      const count = parseInt(row.total);
      dirMap[dir].total += count;
      if (row.outcome === 'WINNER') dirMap[dir].winners += count;
      if (row.outcome === 'UNFORCED_ERROR') dirMap[dir].errors += count;
      if (row.outcome === 'FORCED_ERROR') dirMap[dir].errors += count;
      if (row.outcome === 'CONTINUE') dirMap[dir].inPlay += count;
    }

    const serveData = Object.values(dirMap).map(d => {
      const winCI = wilsonInterval(d.winners, d.total);
      const errCI = wilsonInterval(d.errors, d.total);
      return {
        ...d,
        winnerRate: d.total > 0 ? Math.round((d.winners / d.total) * 1000) / 1000 : 0,
        errorRate: d.total > 0 ? Math.round((d.errors / d.total) * 1000) / 1000 : 0,
        winnerCI: winCI,
        errorCI: errCI,
        confidence: d.total >= 30 ? 'high' : d.total >= 10 ? 'low' : 'insufficient',
      };
    });

    res.json(serveData);
  } catch (err) {
    console.error('Serve error:', err);
    res.status(500).json({ error: err.message });
  }
});

// ─── Serve + 1 Patterns ─────────────────────────────────────────────
app.get('/api/player/:name/serve-plus-one', async (req, res) => {
  try {
    const playerName = req.params.name;
    let params = [playerName];
    let paramIdx = 2;
    let filterResult = buildFilterClause(req.query, params, paramIdx);
    params = filterResult.params;
    let filterSQL = filterResult.clauses.length > 0
      ? 'AND ' + filterResult.clauses.join(' AND ')
      : '';

    const result = await pool.query(`
      SELECT
        serve.serve_direction::text AS serve_dir,
        next_shot.shot_type::text AS response_type,
        next_shot.direction::text AS response_dir,
        next_shot.outcome::text AS outcome,
        COUNT(*) AS total
      FROM shot serve
      JOIN shot next_shot ON serve.point_number = next_shot.point_number
        AND serve.point_match_id = next_shot.point_match_id
        AND next_shot.number = 1
      JOIN point p ON serve.point_number = p.number AND serve.point_match_id = p.match_id
      JOIN match m ON p.match_id = m.id
      WHERE serve.number = 0
        AND (m.first_player_name = $1 OR m.second_player_name = $1)
        AND serve.serve_direction != 'UNKNOWN_SERVE_DIRECTION'
        ${filterSQL}
      GROUP BY serve.serve_direction, next_shot.shot_type, next_shot.direction, next_shot.outcome
      ORDER BY total DESC
      LIMIT 50
    `, params);

    // Aggregate into serve_dir → response combos
    const combos = {};
    for (const row of result.rows) {
      const key = `${row.serve_dir}→${row.response_type}`;
      if (!combos[key]) {
        combos[key] = {
          serveDir: row.serve_dir,
          responseType: row.response_type,
          total: 0,
          winners: 0,
          errors: 0,
        };
      }
      const count = parseInt(row.total);
      combos[key].total += count;
      if (row.outcome === 'WINNER') combos[key].winners += count;
      if (row.outcome === 'UNFORCED_ERROR' || row.outcome === 'FORCED_ERROR') combos[key].errors += count;
    }

    const serveOneData = Object.values(combos)
      .filter(c => c.total >= 5)
      .map(c => ({
        ...c,
        winnerRate: c.total > 0 ? Math.round((c.winners / c.total) * 1000) / 1000 : 0,
        errorRate: c.total > 0 ? Math.round((c.errors / c.total) * 1000) / 1000 : 0,
        winnerCI: wilsonInterval(c.winners, c.total),
        errorCI: wilsonInterval(c.errors, c.total),
        confidence: c.total >= 30 ? 'high' : c.total >= 10 ? 'low' : 'insufficient',
      }))
      .sort((a, b) => b.total - a.total);

    res.json(serveOneData);
  } catch (err) {
    console.error('Serve+1 error:', err);
    res.status(500).json({ error: err.message });
  }
});

// ─── Win vs Loss Comparison ──────────────────────────────────────────
app.get('/api/player/:name/compare', async (req, res) => {
  try {
    const playerName = req.params.name;
    let params = [playerName];
    let paramIdx = 2;
    let filterResult = buildFilterClause(req.query, params, paramIdx);
    params = filterResult.params;
    let filterSQL = filterResult.clauses.length > 0
      ? 'AND ' + filterResult.clauses.join(' AND ')
      : '';

    // We need to figure out if a player won or lost the match.
    // In this dataset the match_id format encodes the winner as first_player.
    // So: if player is first_player_name → they won. If second → they lost.
    const result = await pool.query(`
      WITH match_outcome AS (
        SELECT
          m.id,
          CASE WHEN m.first_player_name = $1 THEN 'win' ELSE 'loss' END AS result
        FROM match m
        WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
        ${filterSQL}
      ),
      shot_data AS (
        SELECT
          mo.result,
          s.shot_type::text AS shot_type,
          s.outcome::text AS outcome,
          COUNT(*) AS cnt
        FROM match_outcome mo
        JOIN point p ON p.match_id = mo.id
        JOIN shot s ON s.point_number = p.number AND s.point_match_id = p.match_id
        WHERE s.shot_type != 'UNKNOWN_SHOT_TYPE'
        GROUP BY mo.result, s.shot_type, s.outcome
      )
      SELECT * FROM shot_data
      ORDER BY result, shot_type, cnt DESC
    `, params);

    // Split into win/loss
    const winPatterns = {};
    const lossPatterns = {};

    for (const row of result.rows) {
      const target = row.result === 'win' ? winPatterns : lossPatterns;
      if (!target[row.shot_type]) {
        target[row.shot_type] = { shotType: row.shot_type, total: 0, winners: 0, errors: 0 };
      }
      const count = parseInt(row.cnt);
      target[row.shot_type].total += count;
      if (row.outcome === 'WINNER') target[row.shot_type].winners += count;
      if (row.outcome === 'UNFORCED_ERROR' || row.outcome === 'FORCED_ERROR') {
        target[row.shot_type].errors += count;
      }
    }

    const processPatterns = (patterns) =>
      Object.values(patterns)
        .map(p => ({
          ...p,
          effectiveness: p.total > 0 ? Math.round(((p.winners - p.errors) / p.total) * 1000) / 1000 : 0,
          winnerRate: p.total > 0 ? Math.round((p.winners / p.total) * 1000) / 1000 : 0,
          errorRate: p.total > 0 ? Math.round((p.errors / p.total) * 1000) / 1000 : 0,
          winnerCI: wilsonInterval(p.winners, p.total),
          errorCI: wilsonInterval(p.errors, p.total),
          confidence: p.total >= 30 ? 'high' : p.total >= 10 ? 'low' : 'insufficient',
        }))
        .sort((a, b) => b.total - a.total);

    res.json({
      wins: processPatterns(winPatterns),
      losses: processPatterns(lossPatterns),
    });
  } catch (err) {
    console.error('Compare error:', err);
    res.status(500).json({ error: err.message });
  }
});

// ─── Direction Pattern Details ───────────────────────────────────────
app.get('/api/player/:name/direction-patterns', async (req, res) => {
  try {
    const playerName = req.params.name;
    let params = [playerName];
    let paramIdx = 2;
    let filterResult = buildFilterClause(req.query, params, paramIdx);
    params = filterResult.params;
    let filterSQL = filterResult.clauses.length > 0
      ? 'AND ' + filterResult.clauses.join(' AND ')
      : '';

    const result = await pool.query(`
      SELECT
        s.shot_type::text AS shot_type,
        s.direction::text AS direction,
        s.outcome::text AS outcome,
        COUNT(*) AS total
      FROM shot s
      JOIN point p ON s.point_number = p.number AND s.point_match_id = p.match_id
      JOIN match m ON p.match_id = m.id
      WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
        AND s.shot_type != 'UNKNOWN_SHOT_TYPE'
        AND s.direction != 'UNKNOWN_DIRECTION'
        ${filterSQL}
      GROUP BY s.shot_type, s.direction, s.outcome
      ORDER BY shot_type, direction, total DESC
    `, params);

    // Aggregate
    const combos = {};
    for (const row of result.rows) {
      const key = `${row.shot_type}|${row.direction}`;
      if (!combos[key]) {
        combos[key] = { shotType: row.shot_type, direction: row.direction, total: 0, winners: 0, errors: 0 };
      }
      const count = parseInt(row.total);
      combos[key].total += count;
      if (row.outcome === 'WINNER') combos[key].winners += count;
      if (row.outcome === 'UNFORCED_ERROR' || row.outcome === 'FORCED_ERROR') combos[key].errors += count;
    }

    const dirPatterns = Object.values(combos)
      .filter(c => c.total >= 5)
      .map(c => ({
        ...c,
        effectiveness: c.total > 0 ? Math.round(((c.winners - c.errors) / c.total) * 1000) / 1000 : 0,
        winnerRate: c.total > 0 ? Math.round((c.winners / c.total) * 1000) / 1000 : 0,
        errorRate: c.total > 0 ? Math.round((c.errors / c.total) * 1000) / 1000 : 0,
        winnerCI: wilsonInterval(c.winners, c.total),
        errorCI: wilsonInterval(c.errors, c.total),
        confidence: c.total >= 30 ? 'high' : c.total >= 10 ? 'low' : 'insufficient',
      }))
      .sort((a, b) => b.total - a.total);

    res.json(dirPatterns);
  } catch (err) {
    console.error('Direction patterns error:', err);
    res.status(500).json({ error: err.message });
  }
});

// ─── Scouting Insights (auto-generated) ──────────────────────────────
app.get('/api/player/:name/insights', async (req, res) => {
  try {
    const playerName = req.params.name;
    // Fetch comparison data  
    const compareRes = await pool.query(`
      WITH match_outcome AS (
        SELECT m.id,
          CASE WHEN m.first_player_name = $1 THEN 'win' ELSE 'loss' END AS result
        FROM match m
        WHERE m.first_player_name = $1 OR m.second_player_name = $1
      ),
      shot_data AS (
        SELECT mo.result, s.shot_type::text AS shot_type, s.outcome::text AS outcome,
          COUNT(*) AS cnt
        FROM match_outcome mo
        JOIN point p ON p.match_id = mo.id
        JOIN shot s ON s.point_number = p.number AND s.point_match_id = p.match_id
        WHERE s.shot_type != 'UNKNOWN_SHOT_TYPE'
        GROUP BY mo.result, s.shot_type, s.outcome
      )
      SELECT * FROM shot_data
    `, [playerName]);

    const winP = {}, lossP = {};
    for (const row of compareRes.rows) {
      const target = row.result === 'win' ? winP : lossP;
      if (!target[row.shot_type]) target[row.shot_type] = { total: 0, winners: 0, errors: 0 };
      const c = parseInt(row.cnt);
      target[row.shot_type].total += c;
      if (row.outcome === 'WINNER') target[row.shot_type].winners += c;
      if (row.outcome === 'UNFORCED_ERROR' || row.outcome === 'FORCED_ERROR') target[row.shot_type].errors += c;
    }

    const insights = [];
    const allShots = new Set([...Object.keys(winP), ...Object.keys(lossP)]);

    for (const shot of allShots) {
      const w = winP[shot] || { total: 0, winners: 0, errors: 0 };
      const l = lossP[shot] || { total: 0, winners: 0, errors: 0 };
      if (w.total < 30 && l.total < 30) continue;

      const wEff = w.total > 0 ? (w.winners - w.errors) / w.total : 0;
      const lEff = l.total > 0 ? (l.winners - l.errors) / l.total : 0;
      const delta = wEff - lEff;

      const shotLabel = shot.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase());

      if (Math.abs(delta) > 0.05) {
        if (delta > 0.1) {
          insights.push({
            type: 'strength',
            icon: '🟢',
            title: `${shotLabel} is a key weapon in wins`,
            detail: `Effectiveness jumps from ${(lEff * 100).toFixed(1)}% in losses to ${(wEff * 100).toFixed(1)}% in wins (Δ ${(delta * 100).toFixed(1)}pp). N=${w.total}W/${l.total}L.`,
            delta,
          });
        } else if (delta > 0.05) {
          insights.push({
            type: 'strength',
            icon: '🟡',
            title: `${shotLabel} improves in wins`,
            detail: `Effectiveness: ${(wEff * 100).toFixed(1)}% in wins vs ${(lEff * 100).toFixed(1)}% in losses (Δ ${(delta * 100).toFixed(1)}pp). N=${w.total}W/${l.total}L.`,
            delta,
          });
        } else if (delta < -0.1) {
          insights.push({
            type: 'weakness',
            icon: '🔴',
            title: `${shotLabel} collapses in losses`,
            detail: `Effectiveness drops from ${(wEff * 100).toFixed(1)}% in wins to ${(lEff * 100).toFixed(1)}% in losses (Δ ${(delta * 100).toFixed(1)}pp). N=${w.total}W/${l.total}L.`,
            delta,
          });
        } else if (delta < -0.05) {
          insights.push({
            type: 'weakness',
            icon: '🟠',
            title: `${shotLabel} degrades in losses`,
            detail: `Effectiveness: ${(wEff * 100).toFixed(1)}% in wins to ${(lEff * 100).toFixed(1)}% in losses (Δ ${(delta * 100).toFixed(1)}pp). N=${w.total}W/${l.total}L.`,
            delta,
          });
        }
      }
    }

    insights.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
    res.json(insights);
  } catch (err) {
    console.error('Insights error:', err);
    res.status(500).json({ error: err.message });
  }
});

// ─── Start Server ────────────────────────────────────────────────────
const PORT = 3001;
app.listen(PORT, () => {
  console.log(`Tennis Analytics API running on http://localhost:${PORT}`);
});
