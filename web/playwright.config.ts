import { defineConfig, devices } from '@playwright/test';

const programData = process.env.ProgramData;
const systemDrive = process.env.SystemDrive;
const webPort = process.env.OMNIX_E2E22_WEB_PORT || '5173';
const webOrigin = `http://127.0.0.1:${webPort}`;
if (programData && systemDrive && programData.toLowerCase().startsWith('%systemdrive%')) {
  process.env.ProgramData = programData.replace(/^%SystemDrive%/i, systemDrive);
}

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  use: {
    baseURL: webOrigin,
    trace: 'on-first-retry',
  },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${webPort}`,
    url: webOrigin,
    reuseExistingServer: !process.env.CI,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
