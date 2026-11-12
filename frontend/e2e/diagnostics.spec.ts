/**
 * STU-014: the diagnostics view in Chromium. The view and its findings (one of each severity, a schema name with a
 * bidi override) have no serious or critical axe violation in light and dark, English and German; a PROD profile
 * asks D-02 first; every write is JSON; Cancel posts the cancel route and the incomplete result says so. TD-036: the
 * backup hints are listed under "Backups" and pass axe too; "Download Markdown report" saves the server's file under
 * its name by keyboard alone, and a refused report (`studio.report_unavailable`) is the page's text.
 */
import { readFileSync } from 'node:fs';

import { test, expect, type Page } from '@playwright/test';

import { openHarness, seriousViolations, type Theme } from './support';

/** The report of the harness's diagnostics scenario (src/harness/diagnostics.ts). */
const REPORT_FILENAME = 'tabrion-studio-diagnosis-postgresql-dev-20261112T101500Z.md';
const REPORT_MARKDOWN =
  '# tabrion Studio diagnosis report\n\n- Findings: 1 error, 2 warnings, 1 note\n';

async function writes(page: Page) {
  return page.evaluate(
    () =>
      (window as unknown as { writes: { method: string; url: string; contentType: string }[] })
        .writes,
  );
}

const TEXT = {
  en: {
    run: 'Run diagnosis',
    summary: '1 error · 2 warnings · 1 note',
    owner: 'Role owns tables',
    backup: 'Backups',
    archive: 'WAL archiving is off',
  },
  de: {
    run: 'Diagnose starten',
    summary: '1 Fehler · 2 Warnungen · 1 Hinweis',
    owner: 'Die Rolle besitzt Tabellen',
    backup: 'Sicherungen',
    archive: 'WAL-Archivierung ist aus',
  },
};

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as const) {
    test(`${theme}, ${lang}: view and findings pass axe`, async ({ page }) => {
      const text = TEXT[lang];
      await openHarness(page, { scenario: 'diagnostics', path: '/diagnostics', theme, lang });
      await expect(page.getByRole('button', { name: text.run })).toHaveAttribute(
        'aria-disabled',
        'false',
      );
      expect(await seriousViolations(page)).toEqual([]);
      await page.getByRole('button', { name: text.run }).click();
      await expect(page.getByTestId('diagnostics-summary')).toHaveText(text.summary);
      await expect(page.getByTestId('diagnostics-finding')).toHaveCount(4);
      await expect(page.getByRole('heading', { name: text.owner })).toBeVisible();
      // The schema name from the database is escaped (U-1).
      await expect(page.getByTestId('diagnostics-findings')).toContainText('audit\\u202Egol');
      // TD-036: the backup hints in their own section, not counted in the summary.
      const backup = page.getByRole('region', { name: text.backup });
      await expect(backup.getByTestId('diagnostics-hint')).toHaveCount(2);
      await expect(backup.getByRole('heading', { level: 4, name: text.archive })).toBeVisible();
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
  await expect(page.getByRole('button', { name: 'Running…' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
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

test('TD-036: "Download Markdown report" by keyboard saves the server file under its name', async ({
  page,
}) => {
  await openHarness(page, { scenario: 'diagnostics', path: '/diagnostics' });
  const download = page.getByRole('button', { name: 'Download Markdown report' });
  await expect(download).toHaveAttribute('aria-disabled', 'true');
  await page.getByRole('button', { name: 'Run diagnosis' }).click();
  await expect(page.getByTestId('diagnostics-findings')).toBeVisible();
  await expect(download).toHaveAttribute('aria-disabled', 'false');
  // Keyboard only: from "Run again", Tab reaches the download, Enter starts it.
  await page.getByRole('button', { name: 'Run again' }).focus();
  await page.keyboard.press('Tab');
  await expect(download).toBeFocused();
  const saved = page.waitForEvent('download');
  await page.keyboard.press('Enter');
  const file = await saved;
  expect(file.suggestedFilename()).toBe(REPORT_FILENAME);
  expect(readFileSync(await file.path(), 'utf8')).toBe(REPORT_MARKDOWN);
  await expect(page.getByTestId('diagnostics-message')).toHaveText(
    `Report downloaded: ${REPORT_FILENAME}`,
  );
  await expect(download).toBeFocused();
  expect(await seriousViolations(page)).toEqual([]);
  const sent = await writes(page);
  expect(sent.map((w) => `${w.method} ${w.url}`)).toEqual([
    'POST /api/connections',
    'POST /api/connections/c1/diagnostics',
    'POST /api/connections/c1/diagnostics/report',
  ]);
  for (const write of sent) expect(write.contentType).toBe('application/json');
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`${theme}: a refused report (studio.report_unavailable) is the page's text and passes axe`, async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
    await page.goto(
      `/harness.html?scenario=diagnostics&path=/diagnostics&report=unavailable&theme=${theme}`,
    );
    await page.getByRole('button', { name: 'Run diagnosis' }).click();
    await expect(page.getByTestId('diagnostics-findings')).toBeVisible();
    let downloads = 0;
    page.on('download', () => {
      downloads += 1;
    });
    await page.getByRole('button', { name: 'Download JSON report' }).click();
    await expect(page.getByRole('alert')).toContainText(
      'Studio could not read from the server the names the report leaves out, so it made no report.',
    );
    expect(downloads).toBe(0);
    expect(await seriousViolations(page)).toEqual([]);
  });
}
