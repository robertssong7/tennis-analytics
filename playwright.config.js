// Minimal Playwright config for Session 16.4 browser verification.
// Single project, headless chromium-headless-shell (already installed).

module.exports = {
  testDir: './tests/e2e',
  timeout: 180000,
  expect: { timeout: 30000 },
  reporter: [['list'], ['json', { outputFile: '_session164_artifacts/playwright-results.json' }]],
  use: {
    headless: true,
    viewport: { width: 1280, height: 800 },
    ignoreHTTPSErrors: true,
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    { name: 'chromium', use: { channel: undefined, browserName: 'chromium' } },
  ],
};
