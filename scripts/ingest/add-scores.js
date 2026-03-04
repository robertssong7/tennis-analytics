const { Pool } = require('pg');
const https = require('https');
require('dotenv').config({ path: __dirname + '/../../.env' }); // or assume it runs from root

const pool = new Pool({
    user: process.env.DB_USER || 'postgres',
    password: process.env.DB_PASSWORD,
    host: process.env.DB_DOMAIN || process.env.DB_HOST || 'localhost',
    port: process.env.DB_PORT || 5432,
    database: process.env.DB_NAME || 'tennis_analytics',
});

function fetchCSV(url) {
    return new Promise((resolve, reject) => {
        https.get(url, (res) => {
            if (res.statusCode === 404) return resolve(''); // some years might not exist
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => resolve(data));
            res.on('error', reject);
        }).on('error', reject);
    });
}

function parseCSV(csvText) {
    if (!csvText) return [];
    const lines = csvText.split('\n');
    const headers = lines[0].split(',').map(h => h.trim());
    const rows = [];
    for (let i = 1; i < lines.length; i++) {
        if (!lines[i].trim()) continue;
        // Handle commas inside quotes? Jeff's ATP matches rarely have commas in score, but we'll use a simple split
        // actually they do not have quotes usually.
        const vals = lines[i].split(',');
        const row = {};
        headers.forEach((h, idx) => { row[h] = (vals[idx] || '').trim(); });
        rows.push(row);
    }
    return rows;
}

// Convert ATP name "Carlos Alcaraz" to match charting name "Carlos_Alcaraz"
function toChartingName(name) {
    if (!name) return '';
    return name.replace(/\s+/g, '_');
}

async function run() {
    console.log('Ensuring score column exists...');
    await pool.query('ALTER TABLE match ADD COLUMN IF NOT EXISTS score VARCHAR(50);');

    // Find all distinct years we have matches for
    const yearsRes = await pool.query(`SELECT DISTINCT EXTRACT(YEAR FROM date) as yr FROM match WHERE date IS NOT NULL;`);
    const years = yearsRes.rows.map(r => parseInt(r.yr)).filter(y => y >= 1990 && y <= new Date().getFullYear());

    let updated = 0;

    for (const year of years) {
        console.log(`Fetching ATP matches for ${year}...`);
        const csvUrl = `https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_${year}.csv`;
        const csvText = await fetchCSV(csvUrl);
        const matches = parseCSV(csvText);

        if (matches.length === 0) continue;
        console.log(`Parsed ${matches.length} ATP matches. Matching with DB...`);

        const client = await pool.connect();
        try {
            await client.query('BEGIN');

            for (const m of matches) {
                if (!m.score) continue;

                // The charting match first_player_name / second_player_name mapping to winner_name / loser_name
                // In the charting dataset, match names are sometimes just Carlos_Alcaraz, sometimes with trailing like Carlos_Alcaraz_Garfia
                // We will just do a relaxed match on the date/tournament/players if possible, or easiest:
                // just match date + players. Date might have 1-2 days difference depending on tournament schedule.
                // Easiest is to match year and roughly the names.

                const wName = toChartingName(m.winner_name);
                const lName = toChartingName(m.loser_name);

                if (!wName || !lName) continue;

                // Let's match by replacing names in the DB
                // We'll update any match where (first_player_name LIKE winner AND second_player_name LIKE loser) OR vice versa
                // AND extract(YEAR from date) = year AND some part of the date is close?
                // Actually, just extract(YEAR from date) = year and both players is often unique enough for the exact matchup in a year?
                // Wait, they might play multiple times a year. So we must match on event name roughly, or month.
                // Let's match by tourney_date YYYYMMDD -> month and year
                const tourneyDate = m.tourney_date || '';
                if (tourneyDate.length !== 8) continue;
                const month = tourneyDate.substring(4, 6);

                // winner_name="Carlos Alcaraz", charting DB="Carlos_Alcaraz"
                // loser_name="Daniil Medvedev", charting DB="Daniil_Medvedev"

                await client.query(`
          UPDATE match 
          SET score = $1
          WHERE EXTRACT(YEAR FROM date) = $2
            AND EXTRACT(MONTH FROM date) = $3
            AND (
              (first_player_name LIKE '%' || $4 || '%' AND second_player_name LIKE '%' || $5 || '%') OR
              (first_player_name LIKE '%' || $5 || '%' AND second_player_name LIKE '%' || $4 || '%')
            )
            AND score IS NULL
        `, [m.score, year, month, wName.split('_').pop(), lName.split('_').pop()]); // match on last name to be safe
            }

            await client.query('COMMIT');
        } catch (err) {
            await client.query('ROLLBACK');
            console.error(err);
        } finally {
            client.release();
        }

        // Check how many were updated
        const upRes = await pool.query('SELECT COUNT(*) as c FROM match WHERE score IS NOT NULL;');
        console.log(`Total scores updated in DB so far: ${upRes.rows[0].c}`);
    }

    await pool.end();
    console.log('Done mapping scorelines.');
}

run().catch(console.error);
