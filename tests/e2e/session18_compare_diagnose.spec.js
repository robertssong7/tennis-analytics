// Session 18 Phase 1d: Compare Players bug diagnosis (read-only, no fix).
// Loads the production compare page with two players, captures pageerror,
// console errors, failed network requests, and the rendered content state.

const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const SITE = 'https://tennisiq-one.vercel.app';
const OUT = path.join(__dirname, '..', '..', '_session18_artifacts');
fs.mkdirSync(OUT, { recursive: true });

test('compare page renders for Sinner vs Alcaraz', async ({ page }) => {
  const pageErrors = [];
  const consoleErrors = [];
  const failedRequests = [];
  const responses = [];

  page.on('pageerror', (err) => pageErrors.push(String(err)));
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  page.on('requestfailed', (req) => {
    failedRequests.push(`${req.method()} ${req.url()} :: ${req.failure() ? req.failure().errorText : 'unknown'}`);
  });
  page.on('response', (resp) => {
    const u = resp.url();
    if (u.includes('su7vqmgkbd') || u.includes('awsapprunner')) {
      responses.push(`${resp.status()} ${resp.request().method()} ${u}`);
    }
  });

  await page.goto(`${SITE}/compare.html?p1=Jannik%20Sinner&p2=Carlos%20Alcaraz`, { waitUntil: 'domcontentloaded' });

  // Give the in-page go() time to fire and Promise.all to resolve.
  await page.waitForTimeout(15000);

  const contentText = await page.locator('#content').innerText().catch(() => '');
  const contentHTML = await page.locator('#content').innerHTML().catch(() => '');

  await page.screenshot({ path: path.join(OUT, 'compare_players_bug.png'), fullPage: true });

  const report = {
    url: page.url(),
    pageErrors,
    consoleErrors,
    failedRequests,
    backendResponses: responses,
    contentTextSnippet: contentText.slice(0, 1500),
    contentHTMLSnippet: contentHTML.slice(0, 4000),
  };
  fs.writeFileSync(path.join(OUT, 'compare_players_diagnose.json'), JSON.stringify(report, null, 2));

  console.log('=== DIAGNOSE REPORT ===');
  console.log(JSON.stringify(report, null, 2));
});
