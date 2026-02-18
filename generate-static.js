/**
 * generate-static.js
 * 
 * Pre-computes all player analytics data into static JSON files.
 * Output goes to public/data/ — the entire public/ folder is then
 * deployable to GitHub Pages (no server needed).
 *
 * Usage: node generate-static.js
 */

const { Pool } = require('pg');
const fs = require('fs');
const path = require('path');

const pool = new Pool({
  user: 'postgres',
  password: '139071',
  host: 'localhost',
  port: 5432,
  database: 'tennis',
  max: 25,
});

const OUT = path.join(__dirname, 'public', 'data');

// ─── Wilson Score Interval ───────────────────────────────────────────
function wilsonInterval(wins, total, z = 1.96) {
  if (total === 0) return { lower: 0, upper: 0, center: 0 };
  const p = wins / total;
  const denom = 1 + (z * z) / total;
  const center = (p + (z * z) / (2 * total)) / denom;
  const margin = (z * Math.sqrt((p * (1 - p)) / total + (z * z) / (4 * total * total))) / denom;
  return {
    lower: Math.max(0, +(center - margin).toFixed(6)),
    upper: Math.min(1, +(center + margin).toFixed(6)),
    center: +center.toFixed(6),
  };
}

function mkdirp(dir) { fs.mkdirSync(dir, { recursive: true }); }

function writeJSON(filePath, data) {
  fs.writeFileSync(filePath, JSON.stringify(data));
}

// ═══════════════════════════════════════════════════════════════════════
// DATA GENERATORS  (mirror the server.js endpoints)
// ═══════════════════════════════════════════════════════════════════════

async function getCoverage(playerName) {
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

  const totals = await pool.query(`
    SELECT COUNT(DISTINCT m.id) AS total_matches, COUNT(*) AS total_points
    FROM match m JOIN point p ON p.match_id = m.id
    WHERE m.first_player_name = $1 OR m.second_player_name = $1
  `, [playerName]);

  return { breakdown: result.rows, totals: totals.rows[0] };
}

async function getPatterns(playerName) {
  const result = await pool.query(`
    SELECT
      s.shot_type::text AS shot_type,
      s.outcome::text AS outcome,
      COUNT(*) AS cnt
    FROM shot s
    JOIN point p ON s.point_number = p.number AND s.point_match_id = p.match_id
    JOIN match m ON p.match_id = m.id
    WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
      AND s.shot_type != 'UNKNOWN_SHOT_TYPE'
    GROUP BY s.shot_type, s.outcome
  `, [playerName]);

  const agg = {};
  for (const row of result.rows) {
    if (!agg[row.shot_type]) agg[row.shot_type] = { total: 0, winners: 0, uf: 0, fe: 0 };
    const c = parseInt(row.cnt);
    agg[row.shot_type].total += c;
    if (row.outcome === 'WINNER') agg[row.shot_type].winners += c;
    if (row.outcome === 'UNFORCED_ERROR') agg[row.shot_type].uf += c;
    if (row.outcome === 'FORCED_ERROR') agg[row.shot_type].fe += c;
  }

  return Object.entries(agg)
    .map(([st, d]) => {
      const errors = d.uf + d.fe;
      const eff = d.total > 0 ? +((d.winners - errors) / d.total).toFixed(3) : 0;
      return {
        shotType: st, total: d.total,
        winners: d.winners, unforcedErrors: d.uf, forcedErrors: d.fe,
        effectiveness: eff,
        winnerRate: d.total > 0 ? +(d.winners / d.total).toFixed(3) : 0,
        errorRate: d.total > 0 ? +(errors / d.total).toFixed(3) : 0,
        winnerCI: wilsonInterval(d.winners, d.total),
        errorCI: wilsonInterval(errors, d.total),
        confidence: d.total >= 30 ? 'high' : d.total >= 10 ? 'low' : 'insufficient',
      };
    })
    .filter(p => p.total >= 10)
    .sort((a, b) => b.total - a.total);
}

async function getServe(playerName) {
  const result = await pool.query(`
    SELECT s.serve_direction::text AS serve_direction, s.outcome::text AS outcome, COUNT(*) AS total
    FROM shot s
    JOIN point p ON s.point_number = p.number AND s.point_match_id = p.match_id
    JOIN match m ON p.match_id = m.id
    WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
      AND s.number = 0 AND s.serve_direction != 'UNKNOWN_SERVE_DIRECTION'
    GROUP BY s.serve_direction, s.outcome
  `, [playerName]);

  const dirMap = {};
  for (const row of result.rows) {
    const dir = row.serve_direction;
    if (!dirMap[dir]) dirMap[dir] = { direction: dir, total: 0, winners: 0, errors: 0, inPlay: 0 };
    const c = parseInt(row.total);
    dirMap[dir].total += c;
    if (row.outcome === 'WINNER') dirMap[dir].winners += c;
    if (row.outcome === 'UNFORCED_ERROR' || row.outcome === 'FORCED_ERROR') dirMap[dir].errors += c;
    if (row.outcome === 'CONTINUE') dirMap[dir].inPlay += c;
  }

  return Object.values(dirMap).map(d => ({
    ...d,
    winnerRate: d.total > 0 ? +(d.winners / d.total).toFixed(3) : 0,
    errorRate: d.total > 0 ? +(d.errors / d.total).toFixed(3) : 0,
    winnerCI: wilsonInterval(d.winners, d.total),
    errorCI: wilsonInterval(d.errors, d.total),
    confidence: d.total >= 30 ? 'high' : d.total >= 10 ? 'low' : 'insufficient',
  }));
}

async function getServePlusOne(playerName) {
  const result = await pool.query(`
    SELECT serve.serve_direction::text AS serve_dir,
      next_shot.shot_type::text AS response_type,
      next_shot.outcome::text AS outcome, COUNT(*) AS total
    FROM shot serve
    JOIN shot next_shot ON serve.point_number = next_shot.point_number
      AND serve.point_match_id = next_shot.point_match_id AND next_shot.number = 1
    JOIN point p ON serve.point_number = p.number AND serve.point_match_id = p.match_id
    JOIN match m ON p.match_id = m.id
    WHERE serve.number = 0
      AND (m.first_player_name = $1 OR m.second_player_name = $1)
      AND serve.serve_direction != 'UNKNOWN_SERVE_DIRECTION'
    GROUP BY serve.serve_direction, next_shot.shot_type, next_shot.outcome
    ORDER BY total DESC LIMIT 80
  `, [playerName]);

  const combos = {};
  for (const row of result.rows) {
    const key = `${row.serve_dir}→${row.response_type}`;
    if (!combos[key]) combos[key] = { serveDir: row.serve_dir, responseType: row.response_type, total: 0, winners: 0, errors: 0 };
    const c = parseInt(row.total);
    combos[key].total += c;
    if (row.outcome === 'WINNER') combos[key].winners += c;
    if (row.outcome === 'UNFORCED_ERROR' || row.outcome === 'FORCED_ERROR') combos[key].errors += c;
  }

  return Object.values(combos).filter(c => c.total >= 5).map(c => ({
    ...c,
    winnerRate: c.total > 0 ? +(c.winners / c.total).toFixed(3) : 0,
    errorRate: c.total > 0 ? +(c.errors / c.total).toFixed(3) : 0,
    winnerCI: wilsonInterval(c.winners, c.total),
    errorCI: wilsonInterval(c.errors, c.total),
    confidence: c.total >= 30 ? 'high' : c.total >= 10 ? 'low' : 'insufficient',
  })).sort((a, b) => b.total - a.total);
}

async function getCompare(playerName) {
  const result = await pool.query(`
    WITH match_outcome AS (
      SELECT m.id,
        CASE WHEN m.first_player_name = $1 THEN 'win' ELSE 'loss' END AS result
      FROM match m WHERE m.first_player_name = $1 OR m.second_player_name = $1
    )
    SELECT mo.result, s.shot_type::text AS shot_type, s.outcome::text AS outcome, COUNT(*) AS cnt
    FROM match_outcome mo
    JOIN point p ON p.match_id = mo.id
    JOIN shot s ON s.point_number = p.number AND s.point_match_id = p.match_id
    WHERE s.shot_type != 'UNKNOWN_SHOT_TYPE'
    GROUP BY mo.result, s.shot_type, s.outcome
  `, [playerName]);

  const winP = {}, lossP = {};
  for (const row of result.rows) {
    const target = row.result === 'win' ? winP : lossP;
    if (!target[row.shot_type]) target[row.shot_type] = { shotType: row.shot_type, total: 0, winners: 0, errors: 0 };
    const c = parseInt(row.cnt);
    target[row.shot_type].total += c;
    if (row.outcome === 'WINNER') target[row.shot_type].winners += c;
    if (row.outcome === 'UNFORCED_ERROR' || row.outcome === 'FORCED_ERROR') target[row.shot_type].errors += c;
  }

  const proc = (patterns) => Object.values(patterns).map(p => ({
    ...p,
    effectiveness: p.total > 0 ? +((p.winners - p.errors) / p.total).toFixed(3) : 0,
    winnerRate: p.total > 0 ? +(p.winners / p.total).toFixed(3) : 0,
    errorRate: p.total > 0 ? +(p.errors / p.total).toFixed(3) : 0,
    winnerCI: wilsonInterval(p.winners, p.total),
    errorCI: wilsonInterval(p.errors, p.total),
    confidence: p.total >= 30 ? 'high' : p.total >= 10 ? 'low' : 'insufficient',
  })).sort((a, b) => b.total - a.total);

  return { wins: proc(winP), losses: proc(lossP) };
}

async function getDirectionPatterns(playerName) {
  const result = await pool.query(`
    SELECT s.shot_type::text AS shot_type, s.direction::text AS direction,
      s.outcome::text AS outcome, COUNT(*) AS total
    FROM shot s
    JOIN point p ON s.point_number = p.number AND s.point_match_id = p.match_id
    JOIN match m ON p.match_id = m.id
    WHERE (m.first_player_name = $1 OR m.second_player_name = $1)
      AND s.shot_type != 'UNKNOWN_SHOT_TYPE' AND s.direction != 'UNKNOWN_DIRECTION'
    GROUP BY s.shot_type, s.direction, s.outcome
  `, [playerName]);

  const combos = {};
  for (const row of result.rows) {
    const key = `${row.shot_type}|${row.direction}`;
    if (!combos[key]) combos[key] = { shotType: row.shot_type, direction: row.direction, total: 0, winners: 0, errors: 0 };
    const c = parseInt(row.total);
    combos[key].total += c;
    if (row.outcome === 'WINNER') combos[key].winners += c;
    if (row.outcome === 'UNFORCED_ERROR' || row.outcome === 'FORCED_ERROR') combos[key].errors += c;
  }

  return Object.values(combos).filter(c => c.total >= 5).map(c => ({
    ...c,
    effectiveness: c.total > 0 ? +((c.winners - c.errors) / c.total).toFixed(3) : 0,
    winnerRate: c.total > 0 ? +(c.winners / c.total).toFixed(3) : 0,
    errorRate: c.total > 0 ? +(c.errors / c.total).toFixed(3) : 0,
    winnerCI: wilsonInterval(c.winners, c.total),
    errorCI: wilsonInterval(c.errors, c.total),
    confidence: c.total >= 30 ? 'high' : c.total >= 10 ? 'low' : 'insufficient',
  })).sort((a, b) => b.total - a.total);
}

async function getInsights(playerName) {
  const result = await pool.query(`
    WITH match_outcome AS (
      SELECT m.id,
        CASE WHEN m.first_player_name = $1 THEN 'win' ELSE 'loss' END AS result
      FROM match m WHERE m.first_player_name = $1 OR m.second_player_name = $1
    )
    SELECT mo.result, s.shot_type::text AS shot_type, s.outcome::text AS outcome, COUNT(*) AS cnt
    FROM match_outcome mo
    JOIN point p ON p.match_id = mo.id
    JOIN shot s ON s.point_number = p.number AND s.point_match_id = p.match_id
    WHERE s.shot_type != 'UNKNOWN_SHOT_TYPE'
    GROUP BY mo.result, s.shot_type, s.outcome
  `, [playerName]);

  const winP = {}, lossP = {};
  for (const row of result.rows) {
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
          type: 'strength', icon: '🟢', title: `${shotLabel} is a key weapon in wins`,
          detail: `Effectiveness jumps from ${(lEff * 100).toFixed(1)}% in losses to ${(wEff * 100).toFixed(1)}% in wins (Δ ${(delta * 100).toFixed(1)}pp). N=${w.total}W/${l.total}L.`, delta
        });
      } else if (delta > 0.05) {
        insights.push({
          type: 'strength', icon: '🟡', title: `${shotLabel} improves in wins`,
          detail: `Effectiveness: ${(wEff * 100).toFixed(1)}% in wins vs ${(lEff * 100).toFixed(1)}% in losses (Δ ${(delta * 100).toFixed(1)}pp). N=${w.total}W/${l.total}L.`, delta
        });
      } else if (delta < -0.1) {
        insights.push({
          type: 'weakness', icon: '🔴', title: `${shotLabel} collapses in losses`,
          detail: `Effectiveness drops from ${(wEff * 100).toFixed(1)}% in wins to ${(lEff * 100).toFixed(1)}% in losses (Δ ${(delta * 100).toFixed(1)}pp). N=${w.total}W/${l.total}L.`, delta
        });
      } else if (delta < -0.05) {
        insights.push({
          type: 'weakness', icon: '🟠', title: `${shotLabel} degrades in losses`,
          detail: `Effectiveness: ${(wEff * 100).toFixed(1)}% in wins to ${(lEff * 100).toFixed(1)}% in losses (Δ ${(delta * 100).toFixed(1)}pp). N=${w.total}W/${l.total}L.`, delta
        });
      }
    }
  }
  return insights.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
}

// ═══════════════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════════════

async function main() {
  console.log('🎾 TennisIQ Static Export\n');

  // Step 1: Get all players with >= 5 matches
  console.log('Fetching player list (min 5 matches)...');
  const playersResult = await pool.query(`
    SELECT name FROM (
      SELECT first_player_name AS name FROM match
      UNION ALL
      SELECT second_player_name AS name FROM match
    ) all_players
    GROUP BY name
    HAVING COUNT(*) >= 5
    ORDER BY COUNT(*) DESC
  `);
  const players = playersResult.rows.map(r => r.name);
  console.log(`Found ${players.length} players.\n`);

  // Write player index
  mkdirp(OUT);
  writeJSON(path.join(OUT, 'players.json'), players);

  // Step 2: Generate data for each player with rolling concurrency
  const CONCURRENCY = 25;
  let done = 0;
  const total = players.length;
  const errors = [];

  console.log(`Starting export with rolling concurrency ${CONCURRENCY}...\n`);

  async function processPlayer(player) {
    const playerDir = path.join(OUT, 'players', player);
    // optimization: skip if already done? No, let's overwrite to be safe.
    mkdirp(playerDir);

    try {
      const [coverage, patterns, serve, serveOne, compare, directions, insights] = await Promise.all([
        getCoverage(player),
        getPatterns(player),
        getServe(player),
        getServePlusOne(player),
        getCompare(player),
        getDirectionPatterns(player),
        getInsights(player),
      ]);

      writeJSON(path.join(playerDir, 'coverage.json'), coverage);
      writeJSON(path.join(playerDir, 'patterns.json'), patterns);
      writeJSON(path.join(playerDir, 'serve.json'), serve);
      writeJSON(path.join(playerDir, 'serve-plus-one.json'), serveOne);
      writeJSON(path.join(playerDir, 'compare.json'), compare);
      writeJSON(path.join(playerDir, 'direction-patterns.json'), directions);
      writeJSON(path.join(playerDir, 'insights.json'), insights);
    } catch (err) {
      errors.push({ player, error: err.message });
    }

    done++;
    if (done % 20 === 0 || done === total) {
      const pct = ((done / total) * 100).toFixed(0);
      process.stdout.write(`\r  [${pct}%] ${done}/${total} players processed`);
    }
  }

  // Rolling queue
  let active = 0;
  let index = 0;

  await new Promise((resolve) => {
    function next() {
      if (index >= players.length && active === 0) {
        resolve();
        return;
      }

      while (active < CONCURRENCY && index < players.length) {
        active++;
        const p = players[index++];
        processPlayer(p).finally(() => {
          active--;
          next();
        });
      }
    }
    next();
  });

  process.stdout.write('\n');


  if (errors.length > 0) {
    console.log(`\n⚠ ${errors.length} errors:`);
    errors.forEach(e => console.log(`  ${e.player}: ${e.error}`));
  }

  console.log(`\n✅ Static data exported to ${OUT}`);
  console.log(`   ${players.length} players — ready for GitHub Pages deployment.\n`);

  await pool.end();
}

main().catch(err => { console.error('Fatal:', err); process.exit(1); });
