/**
 * STU-014: the diagnostics view in Chromium. The view and its findings (one of each severity, a schema name with a
 * bidi override) have no serious or critical axe violation in light and dark, English and German; a PROD profile
 * asks D-02 first; every write is JSON; Cancel posts the cancel route and the incomplete result says so.
 */
import { test, expect, type Page } from '@playwright/test';

import { openHarness, seriousViolations, type Theme } from './support';

async function writes(page: Page) {
  return page.evaluate(
    () =>
      (window as unknown as { writes: { method: string; url: string; contentType: string }[] })
        .writes,
  );
}

const TEXT = {
  en: { run: 'Run diagnosis', summary: '1 error · 2 warnings · 1 note', owner: 'Role owns tables' },
  de: {
    run: 'Diagnose starten',
    summary: '1 Fehler · 2 Warnungen · 1 Hinweis',
    owner: 'Die Rolle besitzt Tabellen',
  },
};

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as const) {
    test(`${theme}, ${lang}: view and findings pass axe`, async ({ page }) => {
      const text = TEXT[lang];
      await openHarness(page, { scenario: 'diagnostics', path: '/diagnostics', theme, lang });
      await expect(page.getByRole('button', { name: text.run })).toBeEnabled();
      expect(await seriousViolations(page)).toEqual([]);
      await page.getByRole('button', { name: text.run }).click();
      await expect(page.getByTestId('diagnostics-summary')).toHaveText(text.summary);
      await expect(page.getByTestId('diagnostics-finding')).toHaveCount(4);
      await expect(page.getByRole('heading', { name: text.owner })).toBeVisible();
      // The schema name from the database is escaped (U-1).
      await expect(page.getByTestId('diagnostics-findings')).toContainText('audit\\u202Egol');
      expect(await seriousViolations(page)).toEqual([]);
    });
  }
}

test('PROD asks D-02 first; every write is JSON', async ({ page }) => {
  await openHarness(page, { scenario: 'diagnostics', path: '/diagnostics' });
  await page.getByLabel('Connection profile').selectOption('user:measure-central');
  await page.getByRole('button', { name: 'Run diagnosis' }).click();
  const dialog = page.getByRole('dialog', { name: 'Production database' });
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  expect(await writes(page)).toEqual([]);
  await dialog.getByRole('button', { name: 'Connect' }).click();
  await expect(page.getByTestId('diagnostics-findings')).toBeVisible();
  // A PROD profile keeps its PROD badge even when the server names another environment (fail closed).
  await expect(page.getByTestId('diagnostics-diagnosis').getByTestId('env-badge')).toHaveAttribute(
    'data-env',
    'prod',
  );
  const sent = await writes(page);
  expect(sent.map((w) => `${w.method} ${w.url}`)).toEqual([
    'POST /api/connections',
    'POST /api/connections/c1/diagnostics',
  ]);
  for (const write of sent) expect(write.contentType).toBe('application/json');
});

test('Cancel stops a running diagnosis; the result says it is incomplete', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
  await page.goto('/harness.html?scenario=diagnostics&path=/diagnostics&slow=1');
  await page.getByRole('button', { name: 'Run diagnosis' }).click();
  await expect(page.getByRole('button', { name: 'Running…' })).toBeDisabled();
  expect(await seriousViolations(page)).toEqual([]);
  await page.getByRole('button', { name: 'Cancel' }).click();
  await expect(page.getByTestId('diagnostics-incomplete')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Not every check ran' })).toBeVisible();
  expect(await seriousViolations(page)).toEqual([]);
  const sent = await writes(page);
  expect(sent.map((w) => `${w.method} ${w.url}`)).toEqual([
    'POST /api/connections',
    'POST /api/connections/c1/diagnostics',
    'POST /api/connections/c1/diagnostics/cancel',
  ]);
  for (const write of sent) expect(write.contentType).toBe('application/json');
});
