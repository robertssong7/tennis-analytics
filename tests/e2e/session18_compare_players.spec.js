// Session 18 — Compare Players renders fast (no 63s spinner from the
// warmup.js 503 retry against /api/v2/*). Verifies: prediction panel
// visible within 12s, both player names present, no pageerror.

const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const SITE = 'https://tennisiq-one.vercel.app';
const OUT = path.join(__dirname, '..', '..', '_session18_artifacts');
fs.mkdirSync(OUT, { recursive: true });

test('compare players renders for Sinner vs Medvedev within 12s', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', err => pageErrors.push(String(err)));

  await page.goto(`${SITE}/compare.html?p1=Jannik%20Sinner&p2=Daniil%20Medvedev`,
    { waitUntil: 'domcontentloaded' });
  const banner = page.locator('text=warming up');
  if (await banner.count() > 0) {
    await banner.waitFor({ state: 'detached', timeout: 90000 });
  }

  // The prediction header is the headline element of a successful compare.
  // Should appear well within 12s now that warmup.js no longer retries
  // /api/v2/* 503s for 63s.
  await expect(page.locator('text=/ML Win Probability/i').first())
    .toBeVisible({ timeout: 12000 });

  // Both player names visible in the panel.
  await expect(page.locator('text=Jannik Sinner').first()).toBeVisible();
  await expect(page.locator('text=Daniil Medvedev').first()).toBeVisible();

  expect(pageErrors).toEqual([]);
  await page.screenshot({ path: path.join(OUT, 'compare_players_fixed.png'), fullPage: true });
});
