// Session 18 — /api/key-matchups-live now serves upcoming + in_progress
// matches at the current_round from the canonical state. Verifies: at least
// one match returned, players in matchups are not in the withdrawals list,
// round labels are real (SF/F/etc), no pageerror on homepage.

const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const SITE = 'https://tennisiq-one.vercel.app';
const API = 'https://su7vqmgkbd.us-east-1.awsapprunner.com';
const OUT = path.join(__dirname, '..', '..', '_session18_artifacts');
fs.mkdirSync(OUT, { recursive: true });

test('key matchups surface live current-round draw', async ({ request, page }) => {
  const pageErrors = [];

  const km = await (await request.get(`${API}/api/key-matchups-live`)).json();
  expect(Array.isArray(km.matchups)).toBe(true);
  expect(km.matchups.length).toBeGreaterThanOrEqual(1);
  expect(km.current_round).toMatch(/^(SF|F|QF|R16|R32)$/);

  const live = await (await request.get(`${API}/api/live-tournament`)).json();
  const withdrawn = new Set(((live.live && live.live.withdrawals) || []).map(w => w.player));
  for (const m of km.matchups) {
    expect(withdrawn.has(m.player1)).toBe(false);
    expect(withdrawn.has(m.player2)).toBe(false);
    expect(m.predicted_p1_win_prob + m.predicted_p2_win_prob).toBeCloseTo(1, 1);
    expect(['scheduled', 'in_progress', 'completed']).toContain(m.status);
  }

  // Homepage gracefully consumes the same endpoint.
  page.on('pageerror', err => pageErrors.push(err.message));
  await page.goto(`${SITE}/`, { waitUntil: 'domcontentloaded' });
  const banner = page.locator('text=warming up');
  if (await banner.count() > 0) {
    await banner.waitFor({ state: 'detached', timeout: 90000 });
  }
  await page.waitForTimeout(8000);
  expect(pageErrors).toEqual([]);
  await page.screenshot({ path: path.join(OUT, 'key_matchups.png'), fullPage: true });
});
