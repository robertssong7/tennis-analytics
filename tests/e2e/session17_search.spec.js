const { test, expect } = require('@playwright/test');

// Session 17 — Search autocomplete: keyboard nav, selection, mobile.

test('autocomplete dropdown shows player rows for Sin query', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', err => pageErrors.push(err.message));

  await page.goto('https://tennisiq-one.vercel.app');
  await page.waitForLoadState('domcontentloaded');

  const banner = page.locator('text=warming up');
  if (await banner.count() > 0) {
    await banner.waitFor({ state: 'detached', timeout: 90000 });
  }

  const input = page.locator('#sinp');
  await input.waitFor({ state: 'visible', timeout: 30000 });
  await input.click();
  await input.fill('Sin');

  const dropdown = page.locator('#sdd');
  await expect(dropdown).toHaveClass(/open/, { timeout: 15000 });

  const items = page.locator('#sdd .search-item');
  await expect.poll(async () => await items.count(), { timeout: 15000 }).toBeGreaterThan(0);

  // First row should contain "Sinner"
  await expect(items.first()).toContainText('Sinner');

  // Arrow Down then Enter should navigate to the highlighted player profile.
  await input.press('ArrowDown');
  await input.press('Enter');
  await page.waitForLoadState('domcontentloaded');
  expect(page.url()).toContain('player.html?name=');

  expect(pageErrors).toEqual([]);

  await page.screenshot({ path: '_session17_artifacts/search_autocomplete.png', fullPage: false });
});

test('mobile 375px viewport hides nav search and renders mobile nav', async ({ browser }) => {
  // The current nav design hides the search box on screens <= 767px via
  // CSS (`.nav-center {display:none}`). Mobile search UI is on the session
  // 18 backlog. This test pins the current contract: the input is in DOM
  // (so autocomplete.js does not error trying to attach) but hidden, and
  // the nav links remain reachable.
  const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', err => pageErrors.push(err.message));

  await page.goto('https://tennisiq-one.vercel.app');
  await page.waitForLoadState('domcontentloaded');
  const banner = page.locator('text=warming up');
  if (await banner.count() > 0) {
    await banner.waitFor({ state: 'detached', timeout: 90000 });
  }

  const input = page.locator('#sinp');
  await expect(input).toBeHidden({ timeout: 5000 });

  // Nav links are still rendered (Home, Compare, Tournaments, Trends, About).
  await expect(page.locator('a:has-text("Compare")').first()).toBeVisible({ timeout: 10000 });

  expect(pageErrors).toEqual([]);

  await page.screenshot({ path: '_session17_artifacts/search_mobile.png', fullPage: false });
  await context.close();
});
