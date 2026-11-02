import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: '*.spec.mjs',
  globalSetup: './global-setup.mjs',
  // One browser at a time: the tests share two seeded vendors and count their requests.
  workers: 1,
  fullyParallel: false,
  timeout: 90_000,
  reporter: [['list']],
  expect: { timeout: 20_000 },
});
