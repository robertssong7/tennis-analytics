import { Express, Request, Response } from "express";
import { Pool } from "pg";
import {
  buildFilterClause,
  getBaselineWinRate,
  getServeBaselineWinRate,
} from "./helpers";

export function registerPlayerRoutes(app: Express, pool: Pool) {
  app.get("/api/player/:name/coverage", async (req, res) => {
    try {
      const playerName = req.params.name;
      const result = await pool.query(
        `
      SELECT EXTRACT(YEAR FROM m.date)::int AS year, COALESCE(m.surface, 'Unknown') AS surface,
        COUNT(DISTINCT m.id) AS matches, COUNT(DISTINCT p.number || '-' || p.match_id::text) AS points
      FROM match m JOIN point p ON p.match_id = m.id
      WHERE m.first_player_name = $1 OR m.second_player_name = $1
      GROUP BY EXTRACT(YEAR FROM m.date), m.surface ORDER BY year DESC, surface
    `,
        [playerName],
      );
      const totals = await pool.query(
        `
      SELECT COUNT(DISTINCT m.id) AS total_matches, COUNT(*) AS total_points
      FROM match m JOIN point p ON p.match_id = m.id
      WHERE m.first_player_name = $1 OR m.second_player_name = $1
    `,
        [playerName],
      );
      res.json({ breakdown: result.rows, totals: totals.rows[0] });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  app.get("/api/player/:name/patterns", async (req, res) => {
    try {
      const playerName = req.params.name;
      const pwBase = await getBaselineWinRate(pool, playerName, req.query);
      const minN = Number.parseInt(req.query.minN as string, 10) || 15;
      let params = [playerName];
      const filterResult = buildFilterClause(req.query, params, 2);
      params = filterResult.params;
      const filterSQL =
        filterResult.clauses.length > 0
          ? "AND " + filterResult.clauses.join(" AND ")
          : "";

      const result = await pool.query(
        `
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
    `,
        params,
      );

      const patterns = result.rows
        .map((row) => {
          const total = Number.parseInt(row.total, 10);
          const won = Number.parseInt(row.points_won, 10);
          const winnerRate = total > 0 ? won / total : 0;
          return {
            shotType: row.shot_type,
            total,
            winnerRate,
            adjustedEffectiveness: total > 0 ? winnerRate - pwBase : 0,
            confidence:
              total >= 30 ? "high" : total >= minN ? "low" : "insufficient",
          };
        })
        .filter((p) => p.total >= minN);

      res.json(patterns);
    } catch (err: any) {
      console.error(err);
      res.status(500).json({ error: err.message });
    }
  });

  app.get("/api/player/:name/serve", async (req, res) => {
    try {
      const playerName = req.params.name;
      let params = [playerName];
      const filterResult = buildFilterClause(req.query, params, 2);
      params = filterResult.params;
      const filterSQL =
        filterResult.clauses.length > 0
          ? "AND " + filterResult.clauses.join(" AND ")
          : "";

      const result = await pool.query(
        `
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
    `,
        params,
      );

      res.json(
        result.rows.map((r) => {
          const total = Number.parseInt(r.total, 10);
          return {
            direction: r.direction,
            total,
            winnerRate: total > 0 ? Number.parseInt(r.points_won, 10) / total : 0,
            errorRate:
              total > 0 ? Number.parseInt(r.double_faults, 10) / total : 0,
            confidence: total >= 30 ? "high" : "low",
          };
        }),
      );
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  app.get("/api/player/:name/serve-plus-one", async (req, res) => {
    try {
      const playerName = req.params.name;
      const pwBase = await getServeBaselineWinRate(pool, playerName, req.query);
      const minN = Number.parseInt(req.query.minN as string, 10) || 15;
      let params = [playerName];
      const filterResult = buildFilterClause(req.query, params, 2);
      params = filterResult.params;
      const filterSQL =
        filterResult.clauses.length > 0
          ? "AND " + filterResult.clauses.join(" AND ")
          : "";

      const result = await pool.query(
        `
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
    `,
        params,
      );

      res.json(
        result.rows
          .map((r) => {
            const total = Number.parseInt(r.total, 10);
            const winnerRate =
              total > 0 ? Number.parseInt(r.points_won, 10) / total : 0;
            return {
              serveDir: r.serve_dir,
              responseType: r.response_type,
              total,
              winnerRate,
              adjustedEffectiveness: total > 0 ? winnerRate - pwBase : 0,
            };
          })
          .filter((x) => x.total >= minN),
      );
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  app.get(
    "/api/player/:name/direction-patterns",
    async (req: Request, res: Response) => {
      try {
        const playerName = req.params.name as string;
        const pwBase = await getBaselineWinRate(pool, playerName, req.query);
        const minN = Number.parseInt(req.query.minN as string, 10) || 15;
        let params = [playerName];
        const filterResult = buildFilterClause(req.query, params, 2);
        params = filterResult.params;
        const filterSQL =
          filterResult.clauses.length > 0
            ? "AND " + filterResult.clauses.join(" AND ")
            : "";

        const result = await pool.query(
          `
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
    `,
          params,
        );

        res.json(
          result.rows
            .map((r) => {
              const total = Number.parseInt(r.total, 10);
              const winnerRate =
                total > 0 ? Number.parseInt(r.points_won, 10) / total : 0;
              return {
                shotType: r.shot_type,
                direction: r.direction,
                total,
                winnerRate,
                adjustedEffectiveness: total > 0 ? winnerRate - pwBase : 0,
              };
            })
            .filter((x) => x.total >= minN),
        );
      } catch (err: any) {
        res.status(500).json({ error: err.message });
      }
    },
  );

  app.get("/api/player/:name/compare", async (req, res) => {
    try {
      const playerName = req.params.name;
      const pwBase = await getBaselineWinRate(pool, playerName, req.query);
      const minN = Number.parseInt(req.query.minN as string, 10) || 15;
      let params = [playerName];
      const filterResult = buildFilterClause(req.query, params, 2);
      params = filterResult.params;
      const filterSQL =
        filterResult.clauses.length > 0
          ? "AND " + filterResult.clauses.join(" AND ")
          : "";

      const result = await pool.query(
        `
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
    `,
        params,
      );

      const formatPatterns = (rows: any[]) =>
        rows
          .map((r: any) => {
            const total = Number.parseInt(r.total, 10);
            const winnerRate =
              total > 0 ? Number.parseInt(r.points_won, 10) / total : 0;
            return {
              shotType: r.shot_type,
              total,
              winnerRate,
              adjustedEffectiveness: total > 0 ? winnerRate - pwBase : 0,
            };
          })
          .filter((x) => x.total >= minN);

      res.json({
        wins: formatPatterns(result.rows.filter((r) => r.result === "win")),
        losses: formatPatterns(result.rows.filter((r) => r.result === "loss")),
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  app.get("/api/player/:name/insights", async (req, res) => {
    try {
      const playerName = req.params.name;
      const pwBase = await getBaselineWinRate(pool, playerName, req.query);
      const minN = Number.parseInt(req.query.minN as string, 10) || 15;
      let params = [playerName];
      const filterResult = buildFilterClause(req.query, params, 2);
      params = filterResult.params;
      const filterSQL =
        filterResult.clauses.length > 0
          ? "AND " + filterResult.clauses.join(" AND ")
          : "";

      const result = await pool.query(
        `
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
    `,
        params,
      );

      const patterns = result.rows
        .map((row) => {
          const total = Number.parseInt(row.total, 10);
          if (total < minN) return null;
          const winnerRate = Number.parseInt(row.points_won, 10) / total;
          return {
            shotType: row.shot_type,
            total,
            uplift: winnerRate - pwBase,
          };
        })
        .filter((p) => p !== null);

      patterns.sort((a, b) => Math.abs(b.uplift) - Math.abs(a.uplift));
      const top3 = patterns.slice(0, 3).map((p) => {
        const shotLabel = p.shotType
          .replace(/_/g, " ")
          .toLowerCase()
          .replace(/\b\w/g, (c: string) => c.toUpperCase());
        const isStrength = p.uplift > 0;
        return {
          type: isStrength ? "strength" : "weakness",
          icon: isStrength ? "🟢" : "🔴",
          title: `${isStrength ? "Strength" : "Weakness"}: ${shotLabel}`,
          detail: `When using this shot, point win rate is ${p.uplift * 100 > 0 ? "+" : ""}${(p.uplift * 100).toFixed(1)}% vs baseline. Evidence: N=${p.total}`,
        };
      });

      res.json(top3);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  app.get(
    "/api/player/:name/pattern-inference",
    async (req: Request, res: Response) => {
      try {
        const playerName = req.params.name as string;
        const pwBase = await getBaselineWinRate(pool, playerName, req.query);
        const minN = Number.parseInt(req.query.minN as string, 10) || 15;
        let params = [playerName];
        const filterResult = buildFilterClause(req.query, params, 2);
        params = filterResult.params;
        const filterSQL =
          filterResult.clauses.length > 0
            ? "AND " + filterResult.clauses.join(" AND ")
            : "";

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

        const nGrams: Record<
          string,
          { sequence: string[]; total: number; won: number }
        > = {};
        for (const point of result.rows) {
          const seq = point.seq.filter(
            (s: string) => s && s.indexOf("UNKNOWN") === -1,
          );
          const won = point.player_won;

          for (let n = 2; n <= 4; n++) {
            for (let i = 0; i <= seq.length - n; i++) {
              const slice = seq.slice(i, i + n);
              const key = slice.join("|");
              if (!nGrams[key])
                nGrams[key] = { sequence: slice, total: 0, won: 0 };
              nGrams[key].total++;
              nGrams[key].won += won;
            }
          }
        }

        const scored = Object.values(nGrams)
          .map((g) => {
            const winRate = g.total > 0 ? g.won / g.total : 0;
            const uplift = winRate - pwBase;
            const rankScore = uplift * Math.log(g.total);
            return {
              sequence: g.sequence,
              total: g.total,
              winnerRate: winRate,
              uplift,
              rankScore,
            };
          })
          .filter((g) => g.total >= minN);

        scored.sort((a, b) => b.rankScore - a.rankScore);
        const winning = scored.filter((s) => s.uplift > 0).slice(0, 15);
        const losing = scored
          .filter((s) => s.uplift < 0)
          .sort((a, b) => a.rankScore - b.rankScore)
          .slice(0, 15);

        res.json({ winning, losing });
      } catch (err: any) {
        res.status(500).json({ error: err.message });
      }
    },
  );
}
