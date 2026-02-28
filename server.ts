import express from "express";
import { Pool } from "pg";
import cors from "cors";
import dotenv from "dotenv";
import { registerPlayersRoutes } from "./routes/playersRoutes";
import { registerPlayerRoutes } from "./routes/playerRoutes";

dotenv.config();

const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static("docs"));

const pool = new Pool({
  user: "postgres",
  password: process.env.DB_PASSWORD,
  host: process.env.DB_DOMAIN,
  port: Number.parseInt(process.env.DB_PORT ?? "", 10),
  database: process.env.DB_NAME,
});

registerPlayersRoutes(app, pool);
registerPlayerRoutes(app, pool);

const PORT = 3001;
app.listen(PORT, () => {
  console.log(`Tennis Analytics API running on http://localhost:${PORT}`);
});
