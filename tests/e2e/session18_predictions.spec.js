// Session 18 — /api/tournament-predictions is now withdrawal-aware.
// Verifies: Alcaraz NOT in favorites, IS in withdrawn list. Draper IS
// in withdrawn. Favorites sum is sane (each between 0 and 1).

const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const SITE = 'https://tennisiq-one.vercel.app';
const API = 'https://su7vqmgkbd.us-east-1.awsapprunner.com';
const OUT = path.join(__dirname, '..', '..', '_session18_artifacts');
fs.mkdirSync(OUT, { recursive: true });

test('tournament-predictions excludes withdrawn players', async ({ request, page }) => {
  const pageErrors = [];

  const tp = await (await request.get(`${API}/api/tournament-predictions`)).json();
  expect(tp.tournament).toMatch(/Italian Open|Internazionali/i);
  const favNames = (tp.favorites || []).map(f => f.player);
  expect(favNames).not.toContain('Carlos Alcaraz');
  expect(favNames).not.toContain('Jack Draper');

  expect(Array.isArray(tp.withdrawn)).toBe(true);
  expect(tp.withdrawn).toContain('Carlos Alcaraz');
  expect(tp.withdrawn).toContain('Jack Draper');

  // remaining_players should align with favorites (favorites is a subset
  // of remaining_players when state is fresh).
  expect(Array.isArray(tp.remaining_players)).toBe(true);
  for (const f of (tp.favorites || [])) {
    expect(tp.remaining_players).toContain(f.player);
  }

  // Probabilities are well-formed: each in [0, 1].
  for (const f of (tp.favorites || [])) {
    expect(f.win_prob).toBeGreaterThanOrEqual(0);
    expect(f.win_prob).toBeLessThanOrEqual(1);
  }

  // Homepage renders predictions card without a pageerror.
  page.on('pageerror', err => pageErrors.push(err.message));
  await page.goto(`${SITE}/`, { waitUntil: 'domcontentloaded' });
  const banner = page.locator('text=warming up');
  if (await banner.count() > 0) {
    await banner.waitFor({ state: 'detached', timeout: 90000 });
  }
  await page.waitForTimeout(8000);
  expect(pageErrors).toEqual([]);
  await page.screenshot({ path: path.join(OUT, 'predictions.png'), fullPage: true });
});
