import { defineConfig } from '@playwright/test';

// The Electron smoke suite (roadmap MP-6.5): the real app on the staged runtime (build/stage), a fresh data folder
// per run. Slow by nature: the first start runs initdb and every migration.
export default defineConfig({
  testDir: '.',
  testMatch: ['*.spec.ts'],
  timeout: 420_000,
  expect: { timeout: 30_000 },
  workers: 1,
  reporter: [['list']],
});
