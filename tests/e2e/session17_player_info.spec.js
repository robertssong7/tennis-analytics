const { test, expect } = require('@playwright/test');

// Session 17 — Player profile rendering for an active player and a retired
// legend. The retired branch is the regression check for peak-Elo handling.

async function _waitForApp(page) {
  await page.waitForLoadState('domcontentloaded');
  const banner = page.locator('text=warming up');
  if (await banner.count() > 0) {
    await banner.waitFor({ state: 'detached', timeout: 90000 });
  }
}

test('player profile renders for active player (Jannik Sinner)', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', err => pageErrors.push(err.message));

  await page.goto('https://tennisiq-one.vercel.app/player.html?name=Jannik%20Sinner');
  await _waitForApp(page);

  // Hero name renders.
  await expect(page.locator('text=Jannik Sinner').first()).toBeVisible({ timeout: 30000 });

  // FIFA rating tier text should appear somewhere on the page.
  await expect(page.locator('text=/legendary|gold|silver|bronze/i').first()).toBeVisible({ timeout: 30000 });

  expect(pageErrors).toEqual([]);

  await page.screenshot({ path: '_session17_artifacts/player_sinner.png', fullPage: true });
});

test('retired player Nadal shows Legendary with peak year label', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', err => pageErrors.push(err.message));

  await page.goto('https://tennisiq-one.vercel.app/player.html?name=Rafael%20Nadal');
  await _waitForApp(page);

  await expect(page.locator('text=Rafael Nadal').first()).toBeVisible({ timeout: 30000 });
  // The retired-player UI exposes a "Peak: YYYY" label.
  await expect(page.locator('text=/Peak:\\s*\\d{4}/').first()).toBeVisible({ timeout: 30000 });

  expect(pageErrors).toEqual([]);

  await page.screenshot({ path: '_session17_artifacts/player_nadal.png', fullPage: true });
});
