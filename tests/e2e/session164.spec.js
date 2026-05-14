const { test, expect } = require('@playwright/test');

// Test convention for this suite (Session 16.4 close-out):
//
//   - pageerror is the STRICT assertion target. It fires only for
//     uncaught JavaScript exceptions on the page — real product bugs.
//   - console.error is captured as a diagnostic log, NOT asserted. It
//     includes browser noise from network-layer failures (envoy 503
//     without CORS headers during App Runner container cycling), which
//     are infrastructure events, not application bugs. Logging them
//     keeps the signal available for debugging without coupling test
//     pass/fail to deploy-window timing.

test('homepage loads with all sections populated', async ({ page }) => {
  const pageErrors = [];
  const consoleLog = [];
  page.on('pageerror', err => pageErrors.push(err.message));
  page.on('console', msg => {
    if (msg.type() === 'error') consoleLog.push(msg.text());
  });

  await page.goto('https://tennisiq-one.vercel.app');
  // domcontentloaded instead of networkidle: warmup.js polls /ready every 5s
  // while the banner is shown, and the carousel fires 8 parallel fetches each
  // with up to 63s of warmup.js retry budget, so the network does not idle for
  // 500ms during cold-bounce activity. DOM-ready is sufficient; the visible-
  // element check below auto-retries via expect.poll until the carousel
  // hydrates or 90s elapses.
  await page.waitForLoadState('domcontentloaded');

  const banner = page.locator('text=warming up');
  if (await banner.count() > 0) {
    await banner.waitFor({ state: 'detached', timeout: 90000 });
  }

  // Real DOM: carousel renders <div class="fc">…</div> per player (index.html:381).
  // The skeleton placeholder uses .fc-skel; exclude it so we count populated cards only.
  const cards = page.locator('.fc:not(.fc-skel)');
  await expect
    .poll(async () => await cards.count(), { timeout: 90000, intervals: [1000, 2000, 5000] })
    .toBeGreaterThanOrEqual(3);

  await expect(page.locator('text=No match insights available')).toHaveCount(0);

  expect(pageErrors).toEqual([]);

  await page.screenshot({ path: '_session164_artifacts/homepage.png', fullPage: true });

  if (consoleLog.length > 0) {
    console.log('[diagnostic] homepage console errors (not asserted):', consoleLog);
  }
});

test('compare page returns real result', async ({ page }) => {
  const pageErrors = [];
  const consoleLog = [];
  page.on('pageerror', err => pageErrors.push(err.message));
  page.on('console', msg => {
    if (msg.type() === 'error') consoleLog.push(msg.text());
  });

  await page.goto('https://tennisiq-one.vercel.app/compare.html');
  await page.waitForLoadState('networkidle', { timeout: 120000 });

  // Real DOM: #i1 placeholder "Player A (e.g. Sinner)...", #i2 placeholder
  // "Player B (e.g. Alcaraz)...". Submit is Enter on the second field.
  await page.fill('#i1', 'Jannik Sinner');
  await page.fill('#i2', 'Carlos Alcaraz');
  await page.press('#i2', 'Enter');

  await expect(page.locator('text=Players not found')).toHaveCount(0, { timeout: 30000 });
  await expect(page.locator('text=/win.*prob|head.to.head|h2h/i').first()).toBeVisible({ timeout: 30000 });

  expect(pageErrors).toEqual([]);

  await page.screenshot({ path: '_session164_artifacts/compare.png', fullPage: true });

  if (consoleLog.length > 0) {
    console.log('[diagnostic] console errors captured (not asserted):', consoleLog);
  }
});

test('tournament page shows real CPI data', async ({ page }) => {
  const pageErrors = [];
  const consoleLog = [];
  page.on('pageerror', err => pageErrors.push(err.message));
  page.on('console', msg => {
    if (msg.type() === 'error') consoleLog.push(msg.text());
  });

  await page.goto('https://tennisiq-one.vercel.app/tournament.html');
  await page.waitForLoadState('networkidle', { timeout: 120000 });

  await expect(page.locator('text=Start the API to load CPI data')).toHaveCount(0);

  expect(pageErrors).toEqual([]);

  await page.screenshot({ path: '_session164_artifacts/tournament.png', fullPage: true });

  if (consoleLog.length > 0) {
    console.log('[diagnostic] console errors captured (not asserted):', consoleLog);
  }
});
