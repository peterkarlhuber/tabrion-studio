/**
 * QA-S1 section D, "Datenansicht und Export": paging, sorting and filtering `orders` in the data view, no editing,
 * and the CSV export of the S1-11 result in the SQL editor.
 */
import { readFileSync } from 'node:fs';

import type { Page } from '@playwright/test';

import {
  expect,
  openEditor,
  openInExplorer,
  replaceText,
  settled,
  skipWithoutStudio,
  startSession,
  test,
} from './studio';

skipWithoutStudio();

const DEV = 'Shop (DEV)';

/** Show the data of orders; the number of rows on the first page. */
async function showOrders(page: Page): Promise<number> {
  await openInExplorer(page, DEV);
  await page.getByRole('treeitem', { name: 'orders', exact: true }).click();
  await page.getByRole('button', { name: 'Daten anzeigen', exact: true }).click();
  // The checklist's example is "Zeilen 1–100"; a page of orders holds fewer rows when its widest possible values
  // would not fit the browser's memory limit, and then the page says so (viewer-limited).
  const status = page.getByTestId('viewer-status');
  await expect(status).toHaveText(/^Zeilen 1–\d+; weitere Zeilen folgen\.$/);
  const rows = Number(/–(\d+);/.exec((await status.textContent()) ?? '')?.[1]);
  if (rows < 100) {
    await expect(page.getByTestId('viewer-limited')).toHaveText(
      `Seiten hier enthalten höchstens ${rows} Zeilen, damit eine Seite mit den breitesten Werten im Speicherlimit des Browsers bleibt.`,
    );
  }
  return rows;
}

/** The texts of one column of the data grid on the current page. */
async function column(page: Page, index: number): Promise<string[]> {
  const cells = page.getByTestId('viewer').locator(`[role="gridcell"][data-column="${index}"]`);
  // The grid shows "…" in a cell until its value has arrived.
  await expect.poll(() => cells.allTextContents()).not.toContain('…');
  return cells.allTextContents();
}

test('S1-17 "Daten anzeigen" pages with "Weiter" and "Zurück", "Zeilen je Seite" changes the page, no total', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  const page1 = await showOrders(page);
  const status = page.getByTestId('viewer-status');
  await page.getByRole('button', { name: 'Weiter', exact: true }).click();
  await expect(status).toHaveText(`Zeilen ${page1 + 1}–${2 * page1}; weitere Zeilen folgen.`);
  await page.getByRole('button', { name: 'Zurück', exact: true }).click();
  await expect(status).toHaveText(`Zeilen 1–${page1}; weitere Zeilen folgen.`);
  // A page size below the current page: the page gets that many rows.
  const size = page.getByLabel('Zeilen je Seite', { exact: true });
  const options = (await size.locator('option').allTextContents()).map((o) => o.trim());
  const smaller = options.map(Number).filter((n) => n > 0 && n < page1);
  expect(
    smaller.length,
    `a page size below ${page1} in ${JSON.stringify(options)}`,
  ).toBeGreaterThan(0);
  const rows = Math.max(...smaller);
  await size.selectOption({ label: String(rows) });
  await expect(status).toHaveText(`Zeilen 1–${rows}; weitere Zeilen folgen.`);
  await page.getByRole('button', { name: 'Weiter', exact: true }).click();
  await expect(status).toHaveText(`Zeilen ${rows + 1}–${2 * rows}; weitere Zeilen folgen.`);
  // There is no total (on purpose): the page never says 1500 or 1.500.
  await expect(page.getByTestId('viewer')).not.toContainText(/\b1\.?500\b(?! *ms)/);
});

test('S1-18 sort by order_date descending and filter status = shipped: sorted, filtered, back on page 1', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  const page1 = await showOrders(page);
  await page.getByRole('button', { name: 'Weiter', exact: true }).click();
  await expect(page.getByTestId('viewer-status')).toHaveText(new RegExp(`^Zeilen ${page1 + 1}–`));
  // Columns of orders: order_id, customer_id, order_date, status, ...
  const headers = await page.getByTestId('viewer').getByRole('columnheader').allTextContents();
  const dateColumn = headers.findIndex((text) => text.startsWith('order_date'));
  const statusColumn = headers.findIndex((text) => text.startsWith('status'));
  expect(dateColumn).toBeGreaterThanOrEqual(0);
  expect(statusColumn).toBeGreaterThanOrEqual(0);
  await page.getByLabel('Sortieren nach', { exact: true }).selectOption({ label: 'order_date' });
  await page.getByTestId('viewer-direction').selectOption({ label: 'Absteigend' });
  await expect(page.getByTestId('viewer-status')).toHaveText(/^Zeilen 1–/);
  await page.getByRole('button', { name: 'Filter hinzufügen', exact: true }).click();
  await page.getByLabel('Spalte von Filter 1', { exact: true }).selectOption({ label: 'status' });
  await page.getByLabel('Operator von Filter 1', { exact: true }).selectOption({ label: '=' });
  await page.getByLabel('Wert von Filter 1', { exact: true }).fill('shipped');
  await page.getByRole('button', { name: 'Anwenden', exact: true }).click();
  await expect(page.getByTestId('viewer-filters-applied')).toHaveText('(1 angewendet)');
  await expect(page.getByTestId('viewer-status')).toHaveText(/^Zeilen 1–\d+; /);
  await expect
    .poll(async () => new Set(await column(page, statusColumn)))
    .toEqual(new Set(['shipped']));
  const dates = await column(page, dateColumn);
  expect(dates.length).toBeGreaterThan(1);
  expect(dates).toEqual([...dates].sort().reverse());
});

test('S1-19 a double click on a cell edits nothing; there is no button to change, insert or delete rows', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await showOrders(page);
  const grid = page.getByTestId('viewer').getByRole('grid');
  const cell = grid.locator('[role="gridcell"][data-row="0"][data-column="3"]');
  const before = await cell.textContent();
  await cell.dblclick();
  await settled(page);
  await page.keyboard.type('changed');
  await page.keyboard.press('Enter');
  await settled(page);
  await expect(cell).toHaveText(before ?? '');
  await expect(
    grid.locator('input, textarea, select, [contenteditable]:not([contenteditable="false"])'),
  ).toHaveCount(0);
  await expect(page.locator('main [contenteditable="true"]')).toHaveCount(0);
  const editing =
    /\b(bearbeiten|ändern|einfügen|zeile hinzufügen|zeile löschen|zeilen löschen|änderungen speichern|übernehmen|commit|rollback|schreibmodus|entsperren)\b/i;
  await expect(page.getByRole('button', { name: editing })).toHaveCount(0);
  await expect(page.getByRole('link', { name: editing })).toHaveCount(0);
});

test('S1-20 "Exportieren…" as CSV with the fetched rows downloads a file with the same columns and rows', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await openEditor(page, DEV);
  await replaceText(page, 'select order_id, order_date, status from orders where status = :status');
  await page.keyboard.press('Control+Enter');
  const panel = page.getByTestId('param-panel');
  await panel.getByLabel('Typ von :status', { exact: true }).selectOption({ label: 'Text' });
  await panel.getByRole('textbox', { name: ':status' }).fill('paid');
  await page.getByRole('button', { name: 'Ausführen', exact: true }).click();
  await expect(page.getByTestId('result-status')).toHaveText(/^[\d.]+ Zeilen? · /);
  const fetched = Number(
    /^([\d.]+)/
      .exec((await page.getByTestId('result-status').textContent()) ?? '')?.[1]
      ?.replace('.', ''),
  );
  const grid = page.getByRole('grid', { name: 'Ergebnis 1' });
  const idCells = grid.locator('[role="gridcell"][data-column="0"]');
  await expect(idCells).toHaveCount(fetched);
  // The grid shows "…" in a cell until its value has arrived.
  await expect.poll(() => idCells.allTextContents()).not.toContain('…');
  const ids = await idCells.allTextContents();

  await page.getByRole('button', { name: 'Exportieren…', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Exportieren' });
  await dialog.getByRole('radio', { name: 'CSV (für Tabellenkalkulationen)', exact: true }).check();
  await dialog
    .getByRole('radio', { name: `Die geholten Zeilen des Ergebnisses (${fetched})`, exact: true })
    .check();
  const saved = page.waitForEvent('download');
  await dialog.getByRole('button', { name: 'Exportieren', exact: true }).click();
  const download = await saved;
  expect(download.suggestedFilename()).toMatch(/\.csv$/);
  await expect(page.getByText(/^Export gespeichert: /)).toBeVisible();
  const lines = readFileSync(await download.path(), 'utf-8')
    .replace(/^\uFEFF/, '')
    .trimEnd()
    .split(/\r?\n/);
  expect(lines[0]).toBe('order_id,order_date,status');
  expect(lines.length - 1).toBe(fetched);
  expect(
    lines.slice(1).map((line) => line.split(',')[0]),
    `grid ids ${JSON.stringify(ids)}`,
  ).toEqual(ids);
  for (const line of lines.slice(1)) expect(line.endsWith(',paid')).toBe(true);
});
