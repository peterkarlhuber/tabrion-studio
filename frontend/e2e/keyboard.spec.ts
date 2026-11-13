/**
 * STU-052 (08 section 7): the command palette, the shortcut help and F6, keyboard only, in Chromium. The palette and
 * the help are modal (the page behind them is inert), return the focus, and pass axe with colour contrast in light
 * and dark, English and German. The main flows of the MVP run keyboard only from the page's start, without a click
 * and without focusing an element from the test (TD-020): connect (08 section 5, with D-02), the explorer, and a
 * query with a parameter and a cancel in the SQL editor, with F6 through the panes of the explorer and the editor
 * (08 section 7, "Explorer/Editor/Ergebnis").
 */
import { test, expect, type Locator, type Page } from '@playwright/test';

import { openHarness, seriousViolations, type Theme } from './support';

/**
 * Press Tab (or Shift+Tab) until `target` has the focus (at most `limit` times), as a keyboard user would. Past the
 * page's last control the focus leaves the page: Chromium comes back at its start, headless Firefox has no browser UI
 * to go to and keeps the focus (TD-047), so a control before the focus is reached with Shift+Tab.
 */
async function tabTo(
  page: Page,
  target: Locator,
  limit = 40,
  key: 'Tab' | 'Shift+Tab' = 'Tab',
): Promise<void> {
  for (let press = 0; press < limit; press += 1) {
    if (await target.evaluate((element) => element === document.activeElement)) return;
    await page.keyboard.press(key);
  }
  await expect(target).toBeFocused();
}

/** "Go to …" through the palette; the focus lands on the new page's main area. */
async function goTo(page: Page, words: string, heading: string): Promise<void> {
  await page.keyboard.press('Control+k');
  await expect(page.getByTestId('command-palette')).toBeVisible();
  await page.keyboard.type(words);
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
  await expect(page.locator('#main')).toBeFocused();
}

async function openPalette(page: Page) {
  await page.keyboard.press('Control+k');
  const palette = page.getByTestId('command-palette');
  await expect(palette).toBeVisible();
  return palette;
}

test('keyboard only: palette to Settings, F6 through the areas, help and back', async ({
  page,
}) => {
  await openHarness(page, { scenario: 'prod' });
  const palette = await openPalette(page);
  const search = palette.getByRole('combobox');
  await expect(search).toBeFocused();
  await page.keyboard.type('settings');
  await expect(palette.getByRole('option')).toHaveCount(1);
  await page.keyboard.press('Enter');
  await expect(palette).toBeHidden();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
  // The focus is on the new page's main area (STU-053, review I-2 of stu-052-td021-review.md).
  await expect(page.locator('#main')).toBeFocused();

  // F6: status bar, then around: header, navigation (the current page), tabs (the selected one), main area.
  await page.keyboard.press('F6');
  await expect(page.getByRole('contentinfo')).toBeFocused();
  await page.keyboard.press('F6');
  await expect(page.getByRole('link', { name: 'Tabrion Studio' })).toBeFocused();
  await page.keyboard.press('F6');
  await expect(page.getByRole('link', { name: 'Settings' })).toBeFocused();
  await page.keyboard.press('F6');
  await expect(page.getByRole('tab', { selected: true })).toBeFocused();
  await page.keyboard.press('F6');
  await expect(page.locator('#main')).toBeFocused();
  const ring = await page
    .locator('#main')
    .evaluate((el) => getComputedStyle(el).outlineStyle + ' ' + getComputedStyle(el).outlineWidth);
  expect(ring).toBe('solid 2px'); // a keyboard move shows the focus (WCAG 2.4.7)
  await page.keyboard.press('F6');
  await expect(page.getByRole('contentinfo')).toBeFocused();
  await page.keyboard.press('Shift+F6');
  await expect(page.locator('#main')).toBeFocused();

  // Help, then Escape: the focus is back where it was.
  await page.keyboard.press('Control+/');
  const help = page.getByTestId('shortcut-help');
  await expect(help).toBeVisible();
  await expect(help.locator('[data-action="close"]')).toBeFocused();
  // The heading row, the three group rows and eleven shortcuts (four of the page, six of the SQL editor, STU-012, and
  // one of the explorer, STU-010).
  await expect(help.getByRole('row')).toHaveCount(15);
  await page.keyboard.press('Escape');
  await expect(help).toBeHidden();
  await expect(page.locator('#main')).toBeFocused();
});

test('the palette is modal and Escape returns the focus to the opener', async ({ page }) => {
  await openHarness(page);
  const button = page.getByTestId('open-palette');
  await button.focus();
  await page.keyboard.press('Enter');
  const palette = page.getByTestId('command-palette');
  await expect(palette).toBeVisible();
  // The page behind a modal <dialog> is inert: a click there does not reach it.
  const inert = await page.evaluate(() => {
    const dialog = document.querySelector('dialog[open]');
    return dialog !== null && dialog.matches(':modal');
  });
  expect(inert).toBe(true);
  await page.keyboard.press('Tab');
  await expect(palette.getByRole('combobox')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(palette).toBeHidden();
  await expect(button).toBeFocused();
});

test('the theme command changes the theme at once', async ({ page }) => {
  await openHarness(page, { theme: 'light' });
  await openPalette(page);
  await page.keyboard.type('theme dark');
  await page.keyboard.press('Enter');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
});

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as const) {
    test(`${theme}, ${lang}: palette and help have no serious or critical axe violation`, async ({
      page,
    }) => {
      await openHarness(page, { theme, lang });
      await openPalette(page);
      expect(await seriousViolations(page)).toEqual([]);
      await page.keyboard.type('zzz'); // the empty state
      expect(await seriousViolations(page)).toEqual([]);
      await page.keyboard.press('Escape');
      await page.keyboard.press('Control+/');
      await expect(page.getByTestId('shortcut-help')).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
    });
  }
}

test('keyboard only: connect from the start page, with D-02 for PROD (08 section 5)', async ({
  page,
}) => {
  await openHarness(page, { scenario: 'connections' });
  await goTo(page, 'go connections', 'Connections');
  const detail = page.getByTestId('connection-detail');
  await tabTo(page, page.getByTestId('connection-item').filter({ hasText: 'parts-dev' }));
  await page.keyboard.press('Enter');
  await expect(detail).toContainText('parts-dev');
  await tabTo(page, detail.getByRole('button', { name: 'Test connection' }));
  await page.keyboard.press('Enter');
  await expect(detail.getByTestId('connection-outcome')).toContainText('Connection works');
  // "Connect" comes right before "Test connection", the page's last control.
  await tabTo(page, detail.getByRole('button', { name: 'Connect', exact: true }), 1, 'Shift+Tab');
  await page.keyboard.press('Enter');
  await expect(page).toHaveTitle('[DEV] parts-dev – Tabrion Studio');
  // PROD: D-02 asks with the focus on Cancel; Tab and Enter connect.
  await page.keyboard.press('Shift+F6'); // the navigation, then back into the list with F6 and Tab
  await expect(page.getByRole('link', { name: 'Connections' })).toBeFocused();
  await page.keyboard.press('F6');
  await expect(page.locator('#main')).toBeFocused();
  await tabTo(page, page.getByTestId('connection-item').filter({ hasText: 'measure-central' }));
  await page.keyboard.press('Enter');
  await tabTo(page, detail.getByRole('button', { name: 'Connect', exact: true }));
  await page.keyboard.press('Enter');
  const prod = page.getByRole('dialog', { name: 'Production database' });
  await expect(prod.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await tabTo(page, prod.getByRole('button', { name: 'Connect', exact: true }), 5);
  await page.keyboard.press('Enter');
  await expect(page).toHaveTitle('[PROD] measure-central – Tabrion Studio');
  await expect(detail.getByRole('button', { name: 'Use this connection' })).toBeFocused();
  await expect(page.getByTestId('access-mode')).toHaveText(/Writing locked/);
});

test('keyboard only: the explorer, with F6 between the tree and the details', async ({ page }) => {
  await openHarness(page, { scenario: 'explorer' });
  await goTo(page, 'go explorer', 'Explorer');
  await tabTo(page, page.getByTestId('explorer-open'));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('tree')).toBeVisible();
  // F6 from "Open" (between the areas): the tree's current item, then the details, then the status bar.
  await page.keyboard.press('F6');
  await expect(page.getByRole('treeitem', { name: 'Database file' })).toBeFocused();
  // The details before an object is chosen: nothing to focus, so the pane itself (C-1 of stu-052b-review.md: after
  // the choice F6 must still find the selected tab).
  await page.keyboard.press('F6');
  await expect(page.getByTestId('explorer-detail')).toBeFocused();
  await page.keyboard.press('Shift+F6');
  await expect(page.getByRole('treeitem', { name: 'Database file' })).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('p');
  await expect(page.getByRole('treeitem', { name: 'part', exact: true })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('detail-column')).toHaveCount(8);
  await page.keyboard.press('F6');
  await expect(page.getByRole('tab', { name: 'Columns' })).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: 'Constraints' })).toBeFocused();
  await page.keyboard.press('F6');
  await expect(page.getByRole('contentinfo')).toBeFocused();
  await page.keyboard.press('Shift+F6');
  await expect(page.getByRole('tab', { name: 'Constraints', selected: true })).toBeFocused();
  await page.keyboard.press('Shift+F6');
  await expect(page.getByRole('treeitem', { name: 'part', exact: true })).toBeFocused();
  await page.keyboard.press('Shift+F6');
  await expect(page.getByRole('link', { name: 'Explorer' })).toBeFocused();
  // Ctrl+P still reaches the filter from anywhere on the page.
  await page.keyboard.press('Control+p');
  await expect(page.getByTestId('explorer-filter')).toBeFocused();
});

test('keyboard only: a query with a parameter, then a cancel, with F6 through editor, parameters and result', async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
  await page.goto(
    '/harness.html?scenario=editor&path=/sql&theme=light&lang=en&sql=' +
      encodeURIComponent('select * from parts where id = :id'),
  );
  await expect(page.getByTestId('backend-status')).toContainText('0.1.0.dev0');
  await expect(page.locator('.cm-content')).toBeVisible();
  const sql = page.locator('.cm-content');
  // F6 from the page's start: header, navigation (the current page), then the SQL text.
  await page.keyboard.press('F6');
  await expect(page.getByRole('link', { name: 'Tabrion Studio' })).toBeFocused();
  await page.keyboard.press('F6');
  await expect(page.getByRole('link', { name: 'SQL editor' })).toBeFocused();
  await page.keyboard.press('F6');
  await expect(sql).toBeFocused();
  // Before the first run the result is empty: F6 focuses the pane itself (C-1 of stu-052b-review.md: after the run
  // F6 must still reach the grid, checked below).
  const results = page.getByTestId('results');
  await page.keyboard.press('F6');
  await page.keyboard.press('F6');
  await expect(results).toBeFocused();
  await page.keyboard.press('Shift+F6');
  await page.keyboard.press('Shift+F6');
  await expect(sql).toBeFocused();
  await page.keyboard.press('Control+End');
  await page.keyboard.press('Control+Enter');
  // The missing value stops the run and gets the focus (08 section 5).
  const value = page.getByTestId('param-panel').getByRole('textbox', { name: ':id' });
  await expect(value).toBeFocused();
  await page.keyboard.type('42');
  await page.keyboard.press('Shift+F6');
  await expect(sql).toBeFocused();
  await page.keyboard.press('Control+Enter');
  await expect(page.getByTestId('result-status')).toHaveText('3 rows · 25 ms');
  await page.keyboard.press('F6');
  await expect(value).toBeFocused();
  await page.keyboard.press('F6');
  await expect(page.getByRole('grid', { name: 'Result 1' })).toBeFocused();
  await page.keyboard.press('F6');
  await expect(page.getByRole('contentinfo')).toBeFocused();
  await page.keyboard.press('Shift+F6');
  await expect(page.getByRole('grid', { name: 'Result 1' })).toBeFocused();

  // A long query, canceled with Escape from the result (08 section 7), then with Ctrl+. from the SQL text.
  await page.keyboard.press('Shift+F6');
  await page.keyboard.press('Shift+F6');
  await expect(sql).toBeFocused();
  await page.keyboard.press('Control+a');
  await page.keyboard.type('select pg_sleep(60)');
  await page.keyboard.press('Control+Enter');
  await expect(page.getByRole('progressbar', { name: 'Query running' })).toBeVisible();
  await page.keyboard.press('F6');
  await expect(results).toContainText('Running');
  // L-1 of stu-052b-review.md: the focus is in the result, where Escape cancels.
  expect(await results.evaluate((element) => element.contains(document.activeElement))).toBe(true);
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('result-status')).toHaveText(/^Canceled after \d+\.\d s\.$/);
  await page.keyboard.press('Shift+F6');
  await expect(sql).toBeFocused();
  await page.keyboard.press('Control+Enter');
  await expect(page.getByRole('progressbar', { name: 'Query running' })).toBeVisible();
  await page.keyboard.press('Control+.');
  await expect(page.getByTestId('result-status')).toHaveText(/^Canceled after \d+\.\d s\.$/);
  expect(await page.evaluate(() => document.activeElement === document.body)).toBe(false);
});
