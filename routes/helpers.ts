import { Pool } from "pg";

export function buildFilterClause(query: any, params: any, paramIndex: number) {
  const clauses = [];
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
    if (query.side === "Deuce")
      clauses.push(
        `(p.game_score IN ('0-0','15-15','30-30','40-40','15-30','30-15','0-15','15-0','0-30','30-0','40-15','15-40','30-40','40-30'))`,
      );
    else if (query.side === "Ad")
      clauses.push(
        `(p.game_score IN ('40-0','0-40','40-15','15-40','30-40','40-30','40-AD','AD-40'))`,
      );
  }
  return { clauses, params, paramIndex };
}

export async function getServeBaselineWinRate(
  pool: Pool,
  playerName: string,
  queryObj: any,
) {
  let params = [playerName];
  const filterResult = buildFilterClause(queryObj, params, 2);
  params = filterResult.params;
  const filterSQL =
    filterResult.clauses.length > 0
      ? "AND " + filterResult.clauses.join(" AND ")
      : "";

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
  const total = Number.parseInt(res.rows[0]?.total_points || "0", 10);
  const won = Number.parseInt(res.rows[0]?.won_points || "0", 10);
  return total > 0 ? won / total : 0;
}

export async function getBaselineWinRate(
  pool: Pool,
  playerName: string,
  queryObj: any,
) {
  let params = [playerName];
  const filterResult = buildFilterClause(queryObj, params, 2);
  params = filterResult.params;
  const filterSQL =
    filterResult.clauses.length > 0
      ? "AND " + filterResult.clauses.join(" AND ")
      : "";

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
  const total = Number.parseInt(res.rows[0]?.total_points || "0", 10);
  const won = Number.parseInt(res.rows[0]?.won_points || "0", 10);
  return total > 0 ? won / total : 0;
}
