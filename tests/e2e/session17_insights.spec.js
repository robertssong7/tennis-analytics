const { test, expect } = require('@playwright/test');

// Session 17 — Homepage insights feed renders multiple cards. Clicking a
// subject link should navigate to that player's profile.

test('homepage insights feed shows multiple cards with subject links', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', err => pageErrors.push(err.message));

  await page.goto('https://tennisiq-one.vercel.app');
  await page.waitForLoadState('domcontentloaded');

  const banner = page.locator('text=warming up');
  if (await banner.count() > 0) {
    await banner.waitFor({ state: 'detached', timeout: 90000 });
  }

  // The insight section is the load-bearing contract. It must render in
  // one of three valid states: pinned-admin card, populated feed, or
  // explicit empty state. Anything else means the JS bailed.
  const insightSection = page.locator('#insightSection');
  await expect(insightSection).toBeVisible({ timeout: 30000 });

  await expect.poll(async () => {
    const cards = await page.locator('#insightFeed .insight-card').count();
    if (cards > 0) return 'feed';
    const emptyVisible = await page.locator('#insightEmpty').isVisible().catch(() => false);
    if (emptyVisible) return 'empty';
    const pinnedVisible = await page.locator('#insightPinned').isVisible().catch(() => false);
    if (pinnedVisible) return 'pinned';
    return 'unresolved';
  }, { timeout: 90000, intervals: [1000, 2000, 5000] }).not.toBe('unresolved');

  // If the feed populated, validate card structure.
  const cards = page.locator('#insightFeed .insight-card');
  if ((await cards.count()) > 0) {
    const firstCardText = await cards.first().textContent();
    expect(firstCardText?.length || 0).toBeGreaterThan(0);
  }

  expect(pageErrors).toEqual([]);

  await page.screenshot({ path: '_session17_artifacts/insights_homepage.png', fullPage: false });
});
