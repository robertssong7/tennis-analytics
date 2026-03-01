const fs = require('fs');
const file = 'd:/Antigravity Projects/Tennis Analytics Dashboard/tennis-analytics/docs/index.html';
let html = fs.readFileSync(file, 'utf8');

// 1. Update logo link
html = html.replace(
    '<div class="logo">',
    '<div class="logo" id="home-logo" style="cursor:pointer;" onclick="window.location.reload();">'
);

// 2. Update empty-state text
html = html.replace(
    '<h2 style="margin:0 0 8px 0; font-size:28px; color:#f8fafc; font-weight:700;">Global Tour Constellation</h2>',
    '<h2 style="margin:0 0 8px 0; font-size:28px; color:#f8fafc; font-weight:700;">ATP Tour Pattern Network</h2>'
);
html = html.replace(
    'Explore the entire ATP patterned network structure across Serve Hubs.<br />',
    'Explore the average shot patterns across Serve Hubs.<br />'
);
html = html.replace(
    '<strong style="color:#38bdf8;">Search for a player to overlay their scouting network against this\n          baseline.</strong>',
    '<strong style="color:#38bdf8;">Search for a player to scout their data.</strong>'
);

// 3. Rename headers
html = html.replace('<h3>🎾 Shot Pattern Effectiveness</h3>', '<h3>🎾 Shots Played</h3>');
html = html.replace('<h3>⚖️ Win vs Loss Comparison</h3>', '<h3>⚖️ Win Loss Comparison of Shots</h3>');

// 4. Reorder sections.
// We have:
// <section class="player-header" ... </section>
// <div id="radar-module-container"></div>
// <section class="card module-card" id="insights-section" ... </section>
// <section class="card module-card" id="coverage-section"> ... </section>
// <section class="card module-card" id="patterns-section"> ... </section>
// <section class="card module-card" id="serve-section"> ... </section>
// <section class="card module-card" id="serve-one-section"> ... </section>
// <section class="card module-card" id="direction-section"> ... </section>
// <section class="card module-card compare-card" id="compare-section"> ... </section>
// <section class="card module-card" id="inference-section"> ... </section>

// Extract them carefully
const extractSection = (idRegex) => {
    const match = html.match(idRegex);
    if (!match) return '';
    const start = match.index;
    let openTags = 0;
    let i = start;
    while (i < html.length) {
        if (html.slice(i, i + 8) === '<section') openTags++;
        if (html.slice(i, i + 9) === '</section') {
            openTags--;
            if (openTags === 0) {
                return html.slice(start, i + 10);
            }
        }
        i++;
    }
    return '';
};

const insightsSec = extractSection(/<section class="card module-card" id="insights-section"/);
const coverageSec = extractSection(/<section class="card module-card" id="coverage-section"/);
const patternsSec = extractSection(/<section class="card module-card" id="patterns-section"/);
const serveSec = extractSection(/<section class="card module-card" id="serve-section"/);
const serveOneSec = extractSection(/<section class="card module-card" id="serve-one-section"/);
const dirSec = extractSection(/<section class="card module-card" id="direction-section"/);
const compareSec = extractSection(/<section class="card module-card compare-card" id="compare-section"/);
const inferenceSec = extractSection(/<section class="card module-card" id="inference-section"/);

// Remove extracted sections from html
[insightsSec, coverageSec, patternsSec, serveSec, serveOneSec, dirSec, compareSec, inferenceSec].forEach(sec => {
    if (sec) {
        html = html.replace(sec, '');
    }
});

// Build the new main content
const newMainContent = `
    <!-- Player Header -->
    <section class="player-header" id="player-header">
      <h2 id="player-name-display"></h2>
      <div class="player-meta" id="player-meta"></div>
    </section>

    <!-- Data Coverage -->
    ${coverageSec}

    <!-- Radar Module Injection Target -->
    <div id="radar-module-container"></div>

    <!-- Top 3 Scout Notes -->
    ${insightsSec}

    <!-- Pattern Inference (New) -->
    ${inferenceSec}

    <!-- Shot Patterns -->
    ${patternsSec}

    <!-- Serve Analysis -->
    ${serveSec}

    <!-- Serve + 1 -->
    ${serveOneSec}

    <!-- Win vs Loss Comparison -->
    ${compareSec}
    
    <!-- Direction Patterns -->
    ${dirSec}
`;

// Replace the old main content (from player-header to the end of main)
const mainStart = html.indexOf('<section class="player-header" id="player-header">');
const mainEnd = html.indexOf('</main>', mainStart);
if (mainStart > -1 && mainEnd > -1) {
    // Keep whatever was inside <main> before player-header if any (unlikely), but just in case
    // wait, I also have <div id="radar-module-container"></div> lying around in the middle.
    // Let me just regex replace the whole interior of <main id="dashboard" class="hidden">
    const mainTagOpen = html.indexOf('<main id="dashboard" class="hidden">') + '<main id="dashboard" class="hidden">'.length;
    html = html.slice(0, mainTagOpen) + '\\n' + newMainContent + '\\n  ' + html.slice(mainEnd);
}

fs.writeFileSync(file, html);
console.log('index.html updated successfully.');
