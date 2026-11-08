/**
 * STU-005 acceptance: axe (WCAG 2.2 A/AA plus best practices, colour contrast included) finds no serious or
 * critical violation, in the light and the dark theme, in English and German: the shell views, the status displays
 * with a PROD connection, every dialog of STU-051 and the sign-in notices.
 */
import { test, expect } from '@playwright/test';

import { openHarness, seriousViolations, type Theme } from './support';

const PAGES: [string, { scenario?: string; path?: string }][] = [
  ['start page', {}],
  ['connections', { path: '/connections' }],
  ['settings', { path: '/settings' }],
  ['not found', { path: '/nowhere' }],
  ['PROD workspace', { scenario: 'prod' }],
  ['TEST workspace', { scenario: 'test' }],
  ['D-01 trust project', { scenario: 'dialog-d01' }],
  ['D-01 hostile and changed (U-1)', { scenario: 'dialog-d01-hostile' }],
  ['D-01 stale, keys removed (I-2)', { scenario: 'dialog-d01-stale' }],
  ['invalid project file notice (I-2)', { scenario: 'project-invalid' }],
  ['D-02 PROD connect', { scenario: 'dialog-d02' }],
  ['D-06 unsaved script', { scenario: 'dialog-d06' }],
  ['D-08 quit', { scenario: 'dialog-d08' }],
  ['sign-in link used', { scenario: 'used' }],
  ['session ended (S-1)', { scenario: 'ended' }],
];

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as const) {
    test.describe(`${theme}, ${lang}`, () => {
      for (const [name, options] of PAGES) {
        test(`${name}: no serious or critical axe violation`, async ({ page }) => {
          await openHarness(page, { ...options, theme, lang });
          if (options.scenario?.startsWith('dialog-')) {
            await expect(page.getByRole('dialog')).toBeVisible();
          }
          if (options.scenario === 'project-invalid') {
            await expect(page.getByTestId('project-invalid')).toBeVisible();
          }
          if (options.scenario === 'ended') {
            await expect(page.getByRole('alert')).toBeVisible();
          }
          expect(await seriousViolations(page)).toEqual([]);
        });
      }
    });
  }
}
