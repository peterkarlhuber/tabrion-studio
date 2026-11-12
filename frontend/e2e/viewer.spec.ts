/**
 * STU-013 page: the data viewer (the explorer's "Data" tab) in Chromium, against the harness's fake viewer. The tab
 * with a page, filters and the cell detail has no serious or critical axe violation in light and dark, English and
 * German; it works with the keyboard alone (Show data, the controls, Next, the grid, the detail, Load in full);
 * values stay text; the page sends no write but the connection open; "SELECT … LIMIT 100" opens the SQL editor.
 */
import { test, expect, type Page } from '@playwright/test';

import { openHarness, seriousViolations, type Theme } from './support';

interface ViewerLog {
  pages: {
    table: string;
    limit: number;
    sort: string | null;
    filter: string | null;
    after: string | null;
  }[];
  cells: { page: string; row: number; column: string }[];
}

const viewerLog = (page: Page) =>
  page.evaluate(() => (window as unknown as { viewerLog: ViewerLog }).viewerLog);

const writes = (page: Page) =>
  page.evaluate(() => (window as unknown as { writes: { method: string; url: string }[] }).writes);

const TEXT = {
  en: {
    data: 'Data',
    show: 'Show data',
    rows: 'Rows 1–100',
    addFilter: 'Add filter',
    next: 'Rows 101–200',
  },
  de: {
    data: 'Daten',
    show: 'Daten anzeigen',
    rows: 'Zeilen 1–100',
    addFilter: 'Filter hinzufügen',
    next: 'Zeilen 101–200',
  },
};

async function openPart(page: Page) {
  await page.getByTestId('explorer-open').click();
  await page.getByRole('treeitem', { name: 'part', exact: true }).click();
  await expect(page.getByTestId('detail-column')).toHaveCount(8);
}

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as const) {
    test(`${theme}, ${lang}: the data tab, its filters and the cell detail pass axe`, async ({
      page,
    }) => {
      test.setTimeout(90_000);
      const text = TEXT[lang];
      await openHarness(page, { scenario: 'explorer', path: '/explorer', theme, lang });
      await openPart(page);
      await page.getByTestId('explorer-show-data').click();
      await expect(page.getByTestId('viewer-status')).toContainText(text.rows);
      await expect(page.getByRole('grid').getByRole('gridcell').first()).toHaveText('1');
      expect(await seriousViolations(page)).toEqual([]);
      await page.getByTestId('viewer-add-filter').click();
      await page.getByTestId('viewer-next').click();
      await expect(page.getByTestId('viewer-status')).toContainText(text.next);
      // Part 7's name is cut on the first page: back, open it, load it in full.
      await page.getByTestId('viewer-previous').click();
      await expect(page.getByTestId('viewer-status')).toContainText(text.rows);
      await page.locator('[data-row="6"][data-column="2"]').click();
      await expect(page.getByTestId('cell-detail')).toBeVisible();
      await page.getByTestId('viewer-load-full').click();
      await expect(page.getByTestId('viewer-full-done')).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
    });
  }
}

test('keyboard only: Show data, the controls, Next, the grid, the detail and Load in full', async ({
  page,
}) => {
  await openHarness(page, { scenario: 'explorer', path: '/explorer' });
  await openPart(page);
  await page.getByTestId('explorer-show-data').focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('tab', { name: 'Data' })).toBeFocused();
  await expect(page.getByTestId('viewer-status')).toContainText('Rows 1–100; more rows follow.');
  // Into the panel: the sort picker comes first (the panel itself is a tab stop of the tabs pattern).
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await expect(page.getByTestId('viewer-sort')).toBeFocused();
  // The direction is disabled until a sort column is chosen: page size, then the search.
  await page.keyboard.press('Tab');
  await expect(page.getByTestId('viewer-page-size')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByTestId('viewer-search')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByTestId('viewer-add-filter')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByLabel('Column of filter 1')).toBeFocused();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await page.keyboard.type('Part 1');
  await page.getByLabel('Operator of filter 1').selectOption('starts_with');
  await page.getByLabel('Column of filter 1').selectOption('name');
  await page.getByLabel('Value of filter 1').press('Enter');
  await expect(page.getByTestId('viewer-filters-applied')).toHaveText('(1 applied)');
  await expect(page.getByTestId('viewer-status')).toContainText('Rows 1–100; more rows follow.');
  const log = await viewerLog(page);
  expect(JSON.parse(log.pages.at(-1)?.filter ?? '[]')).toEqual([
    { column: 'name', op: 'starts_with', value: 'Part 1' },
  ]);
  await page.getByTestId('viewer-next').focus();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('viewer-status')).toContainText(
    'Rows 101–111; this is the last page.',
  );
  // The grid is one tab stop; Enter opens the active cell in the detail, Escape returns to the grid.
  await page.getByRole('grid').focus();
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('Enter');
  await expect(page.locator('#cell-detail-title')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('grid')).toBeFocused();
  expect(await page.evaluate(() => document.activeElement === document.body)).toBe(false);
});

test('a cut value is loaded in full with the keyboard; values stay text', async ({ page }) => {
  await openHarness(page, { scenario: 'explorer', path: '/explorer' });
  await openPart(page);
  await page.getByTestId('explorer-show-data').click();
  await expect(page.getByTestId('viewer-status')).toContainText('Rows 1–100');
  await page.getByRole('grid').focus();
  for (let i = 0; i < 6; i += 1) await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('cell-cut')).toBeVisible();
  await page.getByTestId('viewer-load-full').focus();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('viewer-full-done')).toContainText('5,000 bytes');
  expect((await page.getByTestId('cell-value').textContent())?.length).toBe(5000);
  expect((await viewerLog(page)).cells).toEqual([
    { page: expect.stringMatching(/^vp\d+$/), row: 6, column: 'name' },
  ]);
  // The table with the hostile name: no primary key (rowid paging), hostile values as text.
  await page.getByRole('treeitem', { name: /^notes/ }).click();
  await page.getByTestId('explorer-show-data').click();
  await expect(page.getByTestId('viewer-hint')).toContainText('No primary key');
  await expect(page.getByRole('grid').getByRole('gridcell').first()).toContainText('<img src=x');
  await expect(page.getByTestId('explorer-workspace').locator('img')).toHaveCount(0);
  expect((await writes(page)).map((w) => `${w.method} ${w.url}`)).toEqual([
    'POST /api/connections',
  ]);
});

test('"SELECT … LIMIT 100 in the SQL editor" opens the editor with the statement', async ({
  page,
}) => {
  await openHarness(page, { scenario: 'explorer', path: '/explorer' });
  await openPart(page);
  await page.getByTestId('explorer-to-editor').click();
  await expect(page).toHaveURL(/\/sql$/);
  await expect(page.getByRole('heading', { level: 1, name: 'SQL editor' })).toBeVisible();
  await expect(page.locator('.cm-content')).toContainText('SELECT * FROM "part" LIMIT 100;');
  await expect(page.getByTestId('editor-tab').last()).toContainText('part');
  await expect(page.getByTestId('editor-profile')).toHaveValue('project:parts-dev');
  // Nothing ran: the only write is still the explorer's connection open.
  expect((await writes(page)).map((w) => `${w.method} ${w.url}`)).toEqual([
    'POST /api/connections',
  ]);
});
