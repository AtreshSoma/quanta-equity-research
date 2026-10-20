import { defineConfig, devices } from '@playwright/test';
import * as os from 'os';
import * as path from 'path';

// With no BASE_URL the suite starts its own server on a spare port, with its
// caches, saved scans, regime history and ledger in a temp folder, so a test
// scan never lands in your saved scans or your track record. Set BASE_URL to
// test a server you started yourself (it then writes to that server's data).
const SCRATCH_PORT = 5810;
const scratch = path.join(os.tmpdir(), 'quanta-e2e');
const ownServer = !process.env.BASE_URL;

export default defineConfig({
  webServer: ownServer ? {
    // QUANTA_E2E_PYTHON names the interpreter that has the app's packages when
    // the `python` on PATH is a different one.
    command: `"${process.env.QUANTA_E2E_PYTHON || 'python'}" app.py`,
    url: `http://127.0.0.1:${SCRATCH_PORT}/`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      QUANTA_PORT: String(SCRATCH_PORT),
      QUANTA_CACHE_DIR: path.join(scratch, 'cache'),
      QUANTA_REGIME_HISTORY_PATH: path.join(scratch, 'regime_history.json'),
      QUANTA_LEDGER_PATH: path.join(scratch, 'signals.sqlite3'),
      QUANTA_TRACKING_DISABLED: '1',
    },
  } : undefined,
  testDir: './tests/e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  timeout: 90_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: process.env.BASE_URL || `http://127.0.0.1:${SCRATCH_PORT}`,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop-dark',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 },
        colorScheme: 'dark',
      },
    },
    {
      name: 'desktop-light',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 },
        colorScheme: 'light',
      },
    },
    {
      name: 'tablet',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 820, height: 1180 },
      },
    },
    {
      name: 'mobile',
      use: {
        ...devices['Pixel 7'],
      },
    },
  ],
});
