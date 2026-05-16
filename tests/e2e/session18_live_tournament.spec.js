// Session 18 — /api/live-tournament now reads the canonical state file.
// Verifies: tournament name matches the live Rome event, current_round is
// SF or F (no longer capped at R16), withdrawals include Alcaraz.

const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const SITE = 'https://tennisiq-one.vercel.app';
const API = 'https://su7vqmgkbd.us-east-1.awsapprunner.com';
const OUT = path.join(__dirname, '..', '..', '_session18_artifacts');
fs.mkdirSync(OUT, { recursive: true });

test('live tournament state reads canonical scrape', async ({ request, page }) => {
  const pageErrors = [];

  // API-level assertions first: the contract the frontend will consume.
  const resp = await request.get(`${API}/api/live-tournament`);
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  expect(body.live).toBeTruthy();
  expect(body.live.tournament).toMatch(/Italian Open|Internazionali/i);
  expect(body.live.current_round).toMatch(/^(SF|F)$/);
  expect(Array.isArray(body.live.withdrawals)).toBe(true);
  const withdrawnNames = (body.live.withdrawals || []).map(w => w.player);
  expect(withdrawnNames).toContain('Carlos Alcaraz');
  expect(body.live.data_freshness).toBe('live');

  // Frontend renders the live block on the tournament page.
  page.on('pageerror', err => pageErrors.push(err.message));
  await page.goto(`${SITE}/tournament.html`, { waitUntil: 'domcontentloaded' });
  const banner = page.locator('text=warming up');
  if (await banner.count() > 0) {
    await banner.waitFor({ state: 'detached', timeout: 90000 });
  }
  await expect(page.locator('text=/(Italian Open|Internazionali)/i').first())
    .toBeVisible({ timeout: 60000 });
  expect(pageErrors).toEqual([]);
  await page.screenshot({ path: path.join(OUT, 'live_tournament.png'), fullPage: true });
});
