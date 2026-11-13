import { defineConfig, devices } from '@playwright/test';

/**
 * Browser tests of the shell (STU-005, STU-051): axe with colour contrast in light and dark, the PROD marking, the
 * focus rules of the dialogs and the ended session. They run against the Vite development server and the e2e harness
 * (harness.html, never built into the wheel) with a fake local server; e2e/backend.spec.ts also runs the built
 * frontend in a real `tabrion-studio` process when TABRION_STUDIO_E2E_PYTHON names a Python with Studio installed.
 *
 *     npm run e2e:install                      # once: the Chromium build this Playwright version expects
 *     npx playwright test                      # Chromium from Playwright's browser folder
 *     PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/google-chrome npx playwright test
 *     STUDIO_SCREENSHOTS=<folder> npx playwright test e2e/screenshots.spec.ts
 *
 * Playwright looks for one exact Chromium build; where another build is preinstalled (and downloads are off), set
 * PLAYWRIGHT_CHROMIUM_EXECUTABLE to it instead of reinstalling (docs/development/building.md, review U-5). CI runs
 * these tests in the job `e2e` (.github/workflows/ci.yml), and in Firefox and WebKit in the jobs `e2e (firefox)` and
 * `e2e (webkit)` (STUDIO_E2E_BROWSERS, below); QA-9 adds the platforms smoke.
 */
const port = Number(process.env.STUDIO_E2E_PORT ?? 5175);
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined;

/**
 * The browsers to run (STU-050, TD-047): Chromium by default; STUDIO_E2E_BROWSERS=firefox or =webkit (or a comma list)
 * for the CI jobs "e2e (firefox)" and "e2e (webkit)", which install that browser of the locked Playwright version.
 */
const BROWSERS = {
  chromium: { ...devices['Desktop Chrome'], launchOptions: { executablePath } },
  firefox: { ...devices['Desktop Firefox'] },
  webkit: { ...devices['Desktop Safari'] },
} as const;
type Browser = keyof typeof BROWSERS;
const wanted = (process.env.STUDIO_E2E_BROWSERS ?? 'chromium')
  .split(',')
  .map((name) => name.trim());
for (const name of wanted) {
  if (!(name in BROWSERS)) throw new Error(`STUDIO_E2E_BROWSERS: unknown browser ${name}`);
}

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
  projects: (wanted as Browser[]).map((name) => ({ name, use: BROWSERS[name] })),
  webServer: {
    command: `npx vite --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}/harness.html`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
