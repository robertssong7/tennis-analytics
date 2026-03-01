const fs = require('fs');
const path = require('path');

const ts = Date.now().toString(36); // Short base36 timestamp

const htmlFile = path.join(__dirname, 'docs', 'index.html');
const appJsFile = path.join(__dirname, 'docs', 'app.js');

try {
    // 1. Update index.html
    let htmlStr = fs.readFileSync(htmlFile, 'utf8');
    // Bump style.css and app.js versions
    htmlStr = htmlStr.replace(/style\.css\?v=[^"]+/, `style.css?v=${ts}`);
    htmlStr = htmlStr.replace(/app\.js\?v=[^"]+/, `app.js?v=${ts}`);
    fs.writeFileSync(htmlFile, htmlStr);

    // 2. Update app.js (Dynamic imports)
    let appJsStr = fs.readFileSync(appJsFile, 'utf8');
    appJsStr = appJsStr.replace(/js\/orbitConstellation\/index\.js\?v=[^']+/g, `js/orbitConstellation/index.js?v=${ts}`);
    appJsStr = appJsStr.replace(/js\/playerCompare\/index\.js\?v=[^']+/g, `js/playerCompare/index.js?v=${ts}`);
    appJsStr = appJsStr.replace(/js\/playerRadar\/RadarComponent\.js\?v=[^']+/g, `js/playerRadar/RadarComponent.js?v=${ts}`);
    fs.writeFileSync(appJsFile, appJsStr);

    console.log(`Successfully cache-busted files with hash: ${ts}`);

} catch (e) {
    console.error("Cache busting failed:", e);
}
