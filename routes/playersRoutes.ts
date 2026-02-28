import { Express } from "express";
import { Pool } from "pg";

export function registerPlayersRoutes(app: Express, pool: Pool) {
  app.get("/api/players", async (req, res) => {
    try {
      const q = req.query.q || "";
      const isAll = req.query.all === "true";
      const limitClause = isAll ? "" : "LIMIT 20";
      const result = await pool.query(
        `
      SELECT DISTINCT name FROM (
        SELECT first_player_name AS name FROM match
        UNION
        SELECT second_player_name AS name FROM match
      ) all_players
      WHERE LOWER(name) LIKE LOWER($1)
      ORDER BY name ${limitClause}
    `,
        [`%${q}%`],
      );
      res.json(result.rows.map((r) => r.name));
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });
}
