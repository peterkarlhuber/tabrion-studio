import { resolve } from 'node:path';

import { defineConfig, devices } from '@playwright/test';

/**
 * QA-S1 automation: the cases of owner's manual usability checklist (S1-01 to S1-38 of checklist v2), one test each, against a real
 * `tabrion-studio` per test on a copy of the QA kit's demo project, in a German browser. It catches regressions
 * before the manual pass; it does not replace it. See qa-s1/studio.ts for the environment it needs.
 *
 *     TABRION_STUDIO_E2E_PYTHON=<python> QA_S1_DEMO_BUILDER=<kit>/build_demo.py \
 *       npx playwright test -c qa-s1/playwright.config.ts
 *
 * Reports: the list on the console, the HTML report (QA_S1_REPORT, default qa-s1-results/html) and the results file
 * keyed by case id (QA_S1_RESULTS, default qa-s1-results/results.json; qa-s1/results-reporter.ts). QA_S1_OUTPUT
 * moves all three (default frontend/qa-s1-results, ignored by Git; a relative QA_S1_OUTPUT is taken from the current
 * folder, so all three land in one place).
 */
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined;
const out = resolve(
  process.env.QA_S1_OUTPUT ?? resolve(import.meta.dirname, '..', 'qa-s1-results'),
);

export default defineConfig({
  testDir: '.',
  testMatch: '*.spec.ts',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  outputDir: `${out}/test-output`,
  reporter: [
    ['list'],
    ['html', { open: 'never', outputFolder: process.env.QA_S1_REPORT ?? `${out}/html` }],
    ['./results-reporter.ts', { outputFile: process.env.QA_S1_RESULTS ?? `${out}/results.json` }],
  ],
  use: {
    // The checklist is German; the page picks its language from the browser.
    locale: 'de-DE',
    timezoneId: 'Europe/Vienna',
    trace: 'off',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'], launchOptions: { executablePath } } },
  ],
});
