import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { computeRawScores } from './docs/js/playerRadar/radarModel.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DATA_DIR = path.join(__dirname, 'docs', 'data');
const PLAYERS_FILE = path.join(DATA_DIR, 'players.json');

function safeReadJson(filePath) {
    try {
        if (fs.existsSync(filePath)) {
            return JSON.parse(fs.readFileSync(filePath, 'utf8'));
        }
    } catch (e) { }
    return null;
}

try {
    const players = JSON.parse(fs.readFileSync(PLAYERS_FILE, 'utf8'));
    console.log(`Starting radar distribution calculation for ${players.length} players...`);

    const distributions = {
        serveStrengthProfile: [],
        servePlusOneAdvantage: [],
        defensiveProblemSolving: [],
        finishingConversion: [],
        exploitability: [],
        patternStability: []
    };

    for (const player of players) {
        const playerDir = path.join(DATA_DIR, 'players', player);
        const patterns = safeReadJson(path.join(playerDir, 'patterns.json'));
        const directionPatterns = safeReadJson(path.join(playerDir, 'direction-patterns.json'));
        const servePlusOne = safeReadJson(path.join(playerDir, 'serve-plus-one.json'));

        // Compute axes
        const scores = computeRawScores({ patterns, directionPatterns, servePlusOne });

        if (scores.serveStrengthProfile !== null) distributions.serveStrengthProfile.push(scores.serveStrengthProfile);
        if (scores.servePlusOneAdvantage !== null) distributions.servePlusOneAdvantage.push(scores.servePlusOneAdvantage);
        if (scores.defensiveProblemSolving !== null) distributions.defensiveProblemSolving.push(scores.defensiveProblemSolving);
        if (scores.finishingConversion !== null) distributions.finishingConversion.push(scores.finishingConversion);
        if (scores.exploitability !== null) distributions.exploitability.push(scores.exploitability);
        if (scores.patternStability !== null) distributions.patternStability.push(scores.patternStability);
    }

    const outPath = path.join(DATA_DIR, 'tourDistributionsAll.json');
    fs.writeFileSync(outPath, JSON.stringify(distributions, null, 2));

    // Quick log of distribution sizes to verify data
    for (const key of Object.keys(distributions)) {
        console.log(` - ${key}: ${distributions[key].length} valid players`);
    }
    console.log(`\nSuccessfully wrote distributions to ${outPath}`);

} catch (err) {
    console.error("Error calculating radar distributions:", err);
}
