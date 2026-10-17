import { defineConfig, devices } from '@playwright/test';

// Omnix's trading-terminal spec against the MarketPulse web entry (roadmap Phase 4 gate). The spec mocks
// /api/trading/**; nothing listens behind the dev server's proxy, so other gateway calls fail without a 401 and the
// page stays on the workspace, as in Omnix's own run.
const port = process.env.MARKETPULSE_E2E_WEB_PORT || '5175';
const origin = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: '../tests/e2e',
  testMatch: ['trading-terminal.spec.ts', 'trading-look.spec.ts'],
  // MarketPulse's own look baselines: its terminal fills the window (no Omnix shell bar), so Omnix's do not apply.
  snapshotPathTemplate: '{testDir}/../../marketpulse/look-baselines/{arg}-{projectName}-{platform}{ext}',
  timeout: 45_000,
  expect: { timeout: 7_000 },
  use: { baseURL: origin, trace: 'on-first-retry' },
  webServer: {
    command: `npx vite --config marketpulse/vite.marketpulse.config.ts --host 127.0.0.1 --port ${port} --strictPort`,
    cwd: '..',
    url: origin,
    env: { VITE_GATEWAY_ORIGIN: 'http://127.0.0.1:9' },
    reuseExistingServer: !process.env.CI,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
