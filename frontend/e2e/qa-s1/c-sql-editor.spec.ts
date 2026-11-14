/**
 * QA-S1 section C, "SQL-Editor": a query with a parameter, its result and the detail panel, Format and undo, the
 * project's scripts, the refused write, the history without parameter values, and saving a script.
 */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

import type { Page } from '@playwright/test';

import {
  expect,
  openEditor,
  replaceText,
  settled,
  skipWithoutStudio,
  startSession,
  test,
  type Studio,
} from './studio';

skipWithoutStudio();

const DEV = 'Shop (DEV)';
const QUERY = 'select order_id, order_date, status from orders where status = :status';
const WRITE_SCRIPT = 'skripte/03_schreibversuch_preiserhoehung.sql';

/** Every request that would run a statement on the server. */
function watchRuns(page: Page): string[] {
  const runs: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && path.startsWith('/api/queries')) runs.push(path);
  });
  return runs;
}

/** S1-10 and S1-11: the query with :status, given the value `paid` as Text, run. */
async function runPaid(page: Page): Promise<void> {
  await openEditor(page, DEV);
  await replaceText(page, QUERY);
  await page.keyboard.press('Control+Enter');
  const panel = page.getByTestId('param-panel');
  const value = panel.getByRole('textbox', { name: ':status' });
  await expect(value).toBeFocused();
  await panel.getByLabel('Typ von :status', { exact: true }).selectOption({ label: 'Text' });
  await value.fill('paid');
  await page.getByRole('button', { name: 'Ausführen', exact: true }).click();
  await expect(page.getByRole('tab', { name: /^Ergebnis 1/ })).toBeVisible();
  await expect(page.getByTestId('result-status')).toHaveText(/^[\d.]+ Zeilen · [\d,]+ ms$/);
}

function digest(studio: Studio, relative: string): string {
  return createHash('sha256').update(studio.read(relative)).digest('hex');
}

test('S1-10 running a query with :status runs nothing and asks for a value in "Parameter"', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  const runs = watchRuns(page);
  await openEditor(page, DEV);
  await replaceText(page, QUERY);
  await page.keyboard.press('Control+Enter');
  const panel = page.getByTestId('param-panel');
  await expect(panel.getByRole('heading', { name: 'Parameter', exact: true })).toBeVisible();
  await expect(panel.getByTestId('param-row')).toHaveCount(1);
  await expect(panel.getByRole('textbox', { name: ':status' })).toBeFocused();
  await expect(panel).toContainText('Gib :status einen Wert.');
  await settled(page);
  expect(runs).toEqual([]);
  await expect(page.getByRole('tab', { name: /^Ergebnis 1/ })).toHaveCount(0);
});

test('S1-11 with status = paid as Text, "Ergebnis 1" shows paid rows, count and time; a cell opens at the side', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await runPaid(page);
  const grid = page.getByRole('grid', { name: 'Ergebnis 1' });
  await expect(grid).toBeVisible();
  // Every status cell of the result is "paid".
  const statuses = grid.locator('[role="gridcell"][data-column="2"]');
  // The grid shows "…" in a cell until its value has arrived.
  await expect
    .poll(async () => new Set(await statuses.allTextContents()))
    .toEqual(new Set(['paid']));
  await statuses.first().click();
  await expect(page.getByTestId('cell-detail')).toBeVisible();
  await expect(page.getByTestId('cell-value')).toHaveText('paid');
});

test('S1-12 "Formatieren" upper-cases keywords and keeps :status; Ctrl+Z restores the text', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await openEditor(page, DEV);
  await replaceText(page, QUERY);
  const editor = page.locator('.cm-content');
  const formatted = async () => {
    await expect(page.getByTestId('editor-notice')).toHaveText(
      'Formatiert. Rückgängig mit ⌘Z / Strg+Z.',
    );
    await expect(page.locator('.cm-line').first()).toHaveText('SELECT');
    await expect(editor).toContainText('FROM');
    await expect(editor).toContainText('WHERE');
    await expect(editor).toContainText(':status');
    expect(await page.locator('.cm-line').count()).toBeGreaterThan(1);
  };
  // The keyboard way first, then Ctrl+Z, then the button.
  await page.keyboard.press('Alt+Shift+F');
  await formatted();
  await editor.click();
  await page.keyboard.press('Control+z');
  await expect(editor).toHaveText(QUERY);
  await page.getByRole('button', { name: 'Formatieren', exact: true }).click();
  await formatted();
});

test('S1-13 "Öffnen…" lists the project scripts from skripte/; one opens in a new tab and does not run', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  const runs = watchRuns(page);
  await openEditor(page, DEV);
  const tabs = await page.getByTestId('editor-tab').count();
  await page.getByRole('button', { name: 'Öffnen…', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Skript öffnen' });
  const items = dialog.getByTestId('script-item');
  for (const name of [
    'skripte/01_bestellungen_eines_kunden.sql',
    'skripte/02_umsatz_pro_kategorie.sql',
    'skripte/03_schreibversuch_preiserhoehung.sql',
  ]) {
    await expect(items.filter({ hasText: name })).toHaveCount(1);
  }
  await dialog
    .getByRole('button', { name: 'skripte/02_umsatz_pro_kategorie.sql', exact: true })
    .click();
  await expect(page.getByTestId('editor-tab')).toHaveCount(tabs + 1);
  await expect(page.getByTestId('editor-tab').last()).toContainText('02_umsatz_pro_kategorie');
  await expect(page.locator('.cm-content')).toContainText('Umsatz pro Hauptkategorie');
  await settled(page);
  expect(runs).toEqual([]);
  await expect(page.getByRole('tab', { name: /^Ergebnis 1/ })).toHaveCount(0);
});

test('S1-14 the writing example script is refused on "Shop (DEV)"; the database stays unchanged', async ({
  page,
  studio,
}) => {
  const before = digest(studio, 'daten/shop.sqlite');
  await startSession(page, studio);
  await openEditor(page, DEV);
  await page.getByRole('button', { name: 'Öffnen…', exact: true }).click();
  await page
    .getByRole('dialog', { name: 'Skript öffnen' })
    .getByRole('button', { name: WRITE_SCRIPT, exact: true })
    .click();
  await expect(page.locator('.cm-content')).toContainText('UPDATE products');
  await page.getByRole('button', { name: 'Ausführen', exact: true }).click();
  await expect(page.getByTestId('result-status')).toContainText(
    'Studio führt nur lesende Statements aus: SELECT, WITH',
  );
  expect(digest(studio, 'daten/shop.sqlite')).toBe(before);
  expect(studio.exists('daten/shop.sqlite-journal')).toBe(false);
  expect(studio.exists('daten/shop.sqlite-wal')).toBe(false);
});

test('S1-15 the history (Ctrl+H) shows the query and "Parameter (Werte nicht gespeichert)" but never paid', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await runPaid(page);
  const tabsBefore = await page.getByTestId('editor-tab').count();
  const runs = watchRuns(page);
  await page.keyboard.press('Control+h');
  await expect(page.getByRole('heading', { level: 1, name: 'Abfrageverlauf' })).toBeVisible();
  const entry = page.getByTestId('history-entry').filter({ hasText: QUERY });
  await expect(entry).toHaveCount(1);
  await expect(entry.getByTestId('history-params')).toHaveText(
    /^Parameter \(Werte nicht gespeichert\): :?status$/,
  );
  await expect(page.getByRole('main')).not.toContainText('paid');
  // The button's accessible name adds the entry's time ("Im Editor öffnen (14.11.2026, …)").
  const open = entry.getByRole('button', { name: /^Im Editor öffnen\b/ });
  await open.click();
  await expect(page.getByTestId('editor-tab')).toHaveCount(tabsBefore + 1);
  await expect(page.locator('.cm-content')).toHaveText(QUERY);
  await expect(
    page.getByTestId('param-panel').getByRole('textbox', { name: ':status' }),
  ).toHaveValue('');
  await settled(page);
  expect(runs).toEqual([]);
});

test('S1-16 Ctrl+S saves the tab as skripte/peter-test.sql; closing an unsaved tab asks Save, Discard or Cancel', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await openEditor(page, DEV);
  await replaceText(page, 'select count(*) from orders;\n');
  await page.keyboard.press('Control+s');
  const save = page.getByRole('dialog', { name: 'Skript speichern' });
  await save.getByLabel('Pfad im Projekt', { exact: true }).fill('skripte/peter-test.sql');
  await save.getByRole('button', { name: 'Speichern', exact: true }).click();
  await expect(page.getByTestId('editor-notice')).toHaveText(
    'In skripte/peter-test.sql gespeichert.',
  );
  expect(readFileSync(`${studio.project}/skripte/peter-test.sql`, 'utf-8')).toBe(
    'select count(*) from orders;\n',
  );
  // Change the saved tab and close it.
  await page.locator('.cm-content').click();
  await page.keyboard.press('Control+End');
  await page.keyboard.insertText('-- changed');
  const tab = page.getByTestId('editor-tab').filter({ hasText: 'peter-test' });
  await expect(tab).toContainText('nicht gespeichert');
  await page.getByRole('button', { name: 'peter-test schließen', exact: true }).click();
  const ask = page.getByRole('dialog', { name: 'Skript nicht gespeichert' });
  await expect(ask).toBeVisible();
  for (const name of ['Speichern', 'Verwerfen', 'Abbrechen']) {
    await expect(ask.getByRole('button', { name, exact: true })).toBeVisible();
  }
  await ask.getByRole('button', { name: 'Abbrechen', exact: true }).click();
  await expect(ask).toBeHidden();
  await expect(tab).toHaveCount(1);
});
