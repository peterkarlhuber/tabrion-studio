/**
 * TD-043 (STU-021 in the page): the export dialog of the SQL editor in Chromium against the e2e harness and its fake
 * export route (src/harness/queries.ts). axe with colour contrast in light and dark, English and German, with the
 * dialog open; the whole flow by keyboard (open, choose JSON, export, the file saved under the server's name, the
 * focus back on "Export…"); cancel while the file streams aborts the request and saves nothing. The same export
 * against a real `tabrion-studio` is in e2e/editor-backend.spec.ts.
 */
import { readFileSync } from 'node:fs';

import { test, expect, type Page } from '@playwright/test';

import { seriousViolations, type Theme } from './support';

interface ExportLog {
  exports: { format: string; sql: string; maxRows: number | null; aborted: boolean }[];
}

async function openEditor(
  page: Page,
  sql: string,
  options: { theme?: Theme; lang?: 'en' | 'de' } = {},
): Promise<void> {
  const theme = options.theme ?? 'light';
  await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
  const params = new URLSearchParams({ sql });
  await page.goto(
    `/harness.html?scenario=editor&path=/sql&theme=${theme}&lang=${options.lang ?? 'en'}&${params.toString()}`,
  );
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await expect(page.locator('.cm-content')).toBeVisible();
}

function log(page: Page): Promise<ExportLog> {
  return page.evaluate(() => (window as unknown as { queryLog: ExportLog }).queryLog);
}

const TEXT = {
  en: { run: 'Run', open: 'Export…', dialog: 'Export', start: 'Export' },
  de: { run: 'Ausführen', open: 'Exportieren…', dialog: 'Exportieren', start: 'Exportieren' },
};

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as const) {
    test(`${theme}, ${lang}: the export dialog passes axe`, async ({ page }) => {
      const text = TEXT[lang];
      await openEditor(page, 'select * from generate_series(1, 5)', { theme, lang });
      await page.getByTestId('editor-run').click();
      await expect(page.getByTestId('export-open')).toBeVisible();
      await page.getByTestId('export-open').click();
      const dialog = page.getByRole('dialog', { name: text.dialog });
      await expect(dialog).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
      // The limit check's error state too.
      await dialog.getByTestId('export-limit').fill('0');
      await dialog.getByRole('button', { name: text.start, exact: true }).click();
      await expect(dialog.getByTestId('export-limit')).toHaveAttribute('aria-invalid', 'true');
      expect(await seriousViolations(page)).toEqual([]);
    });
  }
}

test('by keyboard: open, choose JSON, export; the file is saved under its name and the focus returns', async ({
  page,
}) => {
  await openEditor(page, 'select * from generate_series(1, 5)');
  await page.getByTestId('editor-run').click();
  const open = page.getByRole('button', { name: 'Export…' });
  await expect(open).toBeVisible();
  await open.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog', { name: 'Export' });
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  // Tab order: Close (x) wraps; the format radios come first in the body.
  await dialog.getByRole('radio', { name: 'CSV (for spreadsheets)' }).focus();
  await page.keyboard.press('ArrowDown');
  await expect(dialog.getByRole('radio', { name: 'JSON (for programs)' })).toBeChecked();
  await dialog.getByRole('button', { name: 'Export', exact: true }).focus();
  const saved = page.waitForEvent('download');
  await page.keyboard.press('Enter');
  const file = await saved;
  expect(file.suggestedFilename()).toBe('export.json');
  const doc = JSON.parse(readFileSync(await file.path(), 'utf8')) as {
    columns: string[];
    rows: unknown[];
    truncated: string | null;
  };
  expect(doc.rows).toHaveLength(5);
  expect(doc.truncated).toBeNull();
  await expect(dialog).toHaveCount(0);
  await expect(open).toBeFocused();
  await expect(page.getByTestId('editor-notice')).toContainText('Export saved: export.json');
  expect((await log(page)).exports.map((e) => [e.format, e.maxRows])).toEqual([['json', 5]]);
});

test('Cancel while the file streams aborts the request and saves nothing', async ({ page }) => {
  await openEditor(page, 'select export_slow, * from generate_series(1, 3)');
  await page.getByTestId('editor-run').click();
  await page.getByRole('button', { name: 'Export…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Export' });
  let downloads = 0;
  page.on('download', () => {
    downloads += 1;
  });
  const start = dialog.getByRole('button', { name: 'Export', exact: true });
  await start.click();
  await expect(dialog.getByTestId('export-progress')).toContainText('received');
  await expect(start).toHaveAttribute('aria-disabled', 'true');
  expect(await seriousViolations(page)).toEqual([]);
  await dialog.getByRole('button', { name: 'Cancel export' }).click();
  await expect(dialog.getByTestId('export-progress')).toHaveText(
    'The export was canceled; nothing was saved.',
  );
  expect((await log(page)).exports[0]?.aborted).toBe(true);
  expect(downloads).toBe(0);
});
