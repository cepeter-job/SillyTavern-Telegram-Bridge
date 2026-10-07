import {defineConfig} from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: '*.spec.mjs',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45000,
  expect: {timeout: 15000},
  reporter: [['line'], ['html', {open: 'never'}]],
  use: {
    viewport: {width: 390, height: 844},
    isMobile: true,
    hasTouch: true,
    serviceWorkers: 'block',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {name: 'chromium', use: {browserName: 'chromium'}},
    {name: 'webkit', use: {browserName: 'webkit'}},
  ],
});
