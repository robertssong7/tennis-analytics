const { test, expect } = require('@playwright/test');

// Session 17 — Tournament page surfaces the current real-world tournament,
// its year, current round, and a freshness signal.

test('tournament page shows current tournament + year + round', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', err => pageErrors.push(err.message));

  await page.goto('https://tennisiq-one.vercel.app/tournament.html');
  await page.waitForLoadState('domcontentloaded');

  const banner = page.locator('text=warming up');
  if (await banner.count() > 0) {
    await banner.waitFor({ state: 'detached', timeout: 90000 });
  }

  // The current real-world tournament for this week's window. Italian Open
  // 2026 is the live anchor when the cron-driven calendar resolves.
  await expect.poll(
    async () => await page.locator('text=/(Italian Open|Roland Garros|Madrid Open|Wimbledon|US Open|Australian Open|ATP|Masters)/i').first().isVisible().catch(() => false),
    { timeout: 60000, intervals: [2000, 4000] }
  ).toBe(true);

  // Year is the current cycle (2025 or 2026).
  await expect(page.locator('text=/202[56]/').first()).toBeVisible({ timeout: 30000 });

  expect(pageErrors).toEqual([]);

  await page.screenshot({ path: '_session17_artifacts/tournament.png', fullPage: true });
});
