require('dotenv').config();
const { Pool } = require('pg');
const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, '..', '..', 'docs', 'data');

const pool = new Pool({
    user: process.env.DB_USER || 'postgres',
    password: process.env.DB_PASSWORD || 'postgres',
    host: process.env.DB_HOST || 'localhost',
    port: process.env.DB_PORT || 5432,
    database: process.env.DB_NAME || 'tennis_analytics'
});

async function exportEras() {
    try {
        console.log("Extracting player eras from database...");

        // Use the actual 'match' table from tennis analytics charting 
        // to find median active year per player
        // Note: The charting match table typically has match_id like YYYYMMDD-M-Tournament-Round-Player1-Player2
        // We can just extract YYYY from match_id

        const query = `
            WITH player_matches AS (
                SELECT first_player_name as player_name, EXTRACT(YEAR FROM date)::INT as year FROM match WHERE date IS NOT NULL
                UNION ALL
                SELECT second_player_name as player_name, EXTRACT(YEAR FROM date)::INT as year FROM match WHERE date IS NOT NULL
            )
            SELECT player_name, 
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY year) as median_year,
                   MAX(year) as max_year
            FROM player_matches
            GROUP BY player_name
        `;

        const res = await pool.query(query);

        const formatNameId = (name) => {
            return name.toLowerCase().replace(/[^a-z0-9\s-]/g, '').trim().replace(/\s+/g, '_');
        };

        const eras = {};
        for (const row of res.rows) {
            const pid = formatNameId(row.player_name);
            const med = Math.round(row.median_year);
            const max = row.max_year;

            let eraBucket = "1990s";
            if (med >= 2018) eraBucket = "2020s";
            else if (med >= 2008) eraBucket = "2010s";
            else if (med >= 1998) eraBucket = "2000s";

            eras[pid] = {
                median_year: med,
                max_year: max,
                era_bucket: eraBucket
            };
        }

        fs.writeFileSync(path.join(DATA_DIR, 'player_eras.json'), JSON.stringify(eras, null, 2));
        console.log(`Exported eras for ${Object.keys(eras).length} players.`);

    } catch (e) {
        console.error(e);
    } finally {
        pool.end();
    }
}

exportEras();
