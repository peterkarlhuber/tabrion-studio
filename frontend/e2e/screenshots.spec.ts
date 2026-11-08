/**
 * Screenshots of the shell for the review (light and dark, device scale factor 2). Runs only when STUDIO_SCREENSHOTS
 * names an output folder:
 *
 *     STUDIO_SCREENSHOTS=/tmp/shots npx playwright test e2e/screenshots.spec.ts
 */
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

import { test, expect } from '@playwright/test';

import { openHarness, type HarnessOptions, type Theme } from './support';

const folder = process.env.STUDIO_SCREENSHOTS;

test.skip(!folder, 'set STUDIO_SCREENSHOTS to a folder to take the screenshots');
test.use({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2 });

const SHOTS: [string, HarnessOptions][] = [
  ['start', {}],
  ['connections', { path: '/connections' }],
  ['settings', { path: '/settings' }],
  ['workspace-prod', { scenario: 'prod' }],
  ['workspace-test', { scenario: 'test' }],
  ['d01-trust-project', { scenario: 'dialog-d01' }],
  ['d02-prod-connect', { scenario: 'dialog-d02' }],
  ['d06-unsaved-script', { scenario: 'dialog-d06' }],
  ['d08-quit', { scenario: 'dialog-d08' }],
  ['session-ended', { scenario: 'ended' }],
  ['sign-in-used', { scenario: 'used' }],
  ['start-de', { lang: 'de' }],
  ['settings-de', { path: '/settings', lang: 'de' }],
  ['workspace-prod-de', { scenario: 'prod', lang: 'de' }],
  ['d02-prod-connect-de', { scenario: 'dialog-d02', lang: 'de' }],
];

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const [name, options] of SHOTS) {
    test(`${name} (${theme})`, async ({ page }) => {
      await openHarness(page, { ...options, theme });
      if (options.scenario?.startsWith('dialog-')) {
        await expect(page.getByRole('dialog')).toBeVisible();
        // The harness controls are not part of Studio.
        await page.getByTestId('opener').evaluate((el) => {
          (el.parentElement as HTMLElement).style.visibility = 'hidden';
        });
      }
      await page.evaluate(() => document.fonts.ready);
      mkdirSync(folder as string, { recursive: true });
      await page.screenshot({ path: join(folder as string, `stu-005-${name}-${theme}.png`) });
    });
  }
}
