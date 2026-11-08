import { defineConfig, devices } from '@playwright/test';

/**
 * Browser tests of the shell (STU-005, STU-051): axe with colour contrast in light and dark, the PROD marking, the
 * focus rules of the dialogs and the ended session. They run against the Vite development server and the e2e harness
 * (harness.html, never built into the wheel) with a fake local server; e2e/backend.spec.ts also runs the built
 * frontend in a real `tabrion-studio` process when TABRION_STUDIO_E2E_PYTHON names a Python with Studio installed.
 *
 *     npx playwright test                      # Chromium from Playwright's cache
 *     PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/google-chrome npx playwright test
 *     STUDIO_SCREENSHOTS=<folder> npx playwright test e2e/screenshots.spec.ts
 *
 * The CI job for these tests (`e2e`, Chromium plus axe against the built wheel) arrives with TABRION QA-9.
 */
const port = Number(process.env.STUDIO_E2E_PORT ?? 5175);
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    locale: 'en-US',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], launchOptions: { executablePath } },
    },
  ],
  webServer: {
    command: `npx vite --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}/harness.html`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
