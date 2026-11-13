/**
 * STU-012, the SQL editor in Chromium against the e2e harness and its fake query engine (src/harness/queries.ts):
 * run with the keyboard, the result grid (WAI-ARIA grid, keyboard, NULL, cut values, detail panel), parameters, a
 * script with its messages, cancel, a refused write, "load more", editor tabs with drafts and D-06, D-02 for PROD,
 * 100 000 rows within the memory limit of 03 section 10 (300 MB), and axe in light/dark x en/de.
 * The same flows against a real `tabrion-studio` are in e2e/editor-backend.spec.ts.
 */
import { readFileSync } from 'node:fs';

import { test, expect, type Page } from '@playwright/test';

import { openHarness, seriousViolations, type Theme } from './support';

interface QueryLog {
  starts: { sql: string; params: Record<string, unknown>; maxRows: number | null }[];
  rows: { offset: number; limit: number }[];
  cancels: string[];
  connections: string[];
}

async function openEditor(
  page: Page,
  sql: string,
  options: {
    theme?: Theme;
    lang?: 'en' | 'de';
    profile?: string;
    maxRows?: number;
    connectDelay?: number;
    startDelay?: number;
  } = {},
): Promise<void> {
  const params = new URLSearchParams({ sql });
  if (options.profile) params.set('profile', options.profile);
  if (options.maxRows) params.set('maxRows', String(options.maxRows));
  if (options.connectDelay) params.set('connectDelay', String(options.connectDelay));
  if (options.startDelay) params.set('startDelay', String(options.startDelay));
  await page.emulateMedia({ colorScheme: options.theme ?? 'light', reducedMotion: 'reduce' });
  await page.goto(
    `/harness.html?scenario=editor&path=/sql&theme=${options.theme ?? 'light'}&lang=${options.lang ?? 'en'}&${params.toString()}`,
  );
  await expect(page.getByTestId('backend-status')).toContainText('0.1.0.dev0');
  await expect(page.locator('.cm-content')).toBeVisible();
}

function log(page: Page): Promise<QueryLog> {
  return page.evaluate(() => (window as unknown as { queryLog: QueryLog }).queryLog);
}

/** Focus the editor's text and put the cursor at the end. */
async function focusEditor(page: Page): Promise<void> {
  await page.locator('.cm-content').click();
  await page.keyboard.press('Control+End');
}

test('runs the statement at the cursor with Ctrl+Enter and shows the grid', async ({ page }) => {
  await openEditor(page, 'select 1;\nselect * from generate_series(1, 3)');
  await focusEditor(page);
  await page.keyboard.press('Control+Enter');
  await expect(page.getByTestId('result-status')).toHaveText('3 rows · 25 ms');
  await expect(page.getByTestId('result-announce')).toHaveText('3 rows · 25 ms');
  expect((await log(page)).starts.map((s) => s.sql)).toEqual([
    'select * from generate_series(1, 3)',
  ]);
  const grid = page.getByRole('grid', { name: 'Result 1' });
  await expect(grid).toHaveAttribute('aria-rowcount', '4');
  await expect(grid).toHaveAttribute('aria-colcount', '5');
  await expect(grid.getByRole('columnheader')).toHaveText([
    'n, int4',
    'label, text',
    'doc, jsonb',
    'data, bytea',
    'note, text',
  ]);
  const third = grid.getByRole('row').nth(3);
  await expect(third).toHaveAttribute('aria-rowindex', '4');
  await expect(third.getByRole('gridcell').nth(4)).toHaveText('NULL');
  await expect(third.getByRole('gridcell').nth(4)).toHaveCSS('font-style', 'italic');
  await expect(third.getByRole('gridcell').nth(3)).toHaveText('\\xdeadbeef03');
  // A value the server cut: "⋯" and the word for screen readers.
  await expect(grid.getByRole('row').nth(2).getByRole('gridcell').nth(1)).toContainText('cut');
});

test('the grid is one tab stop with arrow keys, Enter opens the detail panel, Escape returns', async ({
  page,
}) => {
  await openEditor(page, 'select * from generate_series(1, 3)');
  await page.getByTestId('editor-run').click();
  const grid = page.getByRole('grid', { name: 'Result 1' });
  await expect(grid.getByRole('gridcell').first()).toHaveText('1');
  await grid.focus();
  const active = () => grid.evaluate((el) => el.getAttribute('aria-activedescendant'));
  const cell = async () =>
    grid.evaluate(
      (el) => document.getElementById(el.getAttribute('aria-activedescendant') ?? '')?.textContent,
    );
  expect(await cell()).toBe('1');
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowDown');
  expect(await cell()).toMatch(/^long text/);
  await page.keyboard.press('End');
  expect(await cell()).toBe('note 2');
  await page.keyboard.press('Control+Home');
  expect(await cell()).toBe('1');
  await page.keyboard.press('Control+End');
  expect(await cell()).toBe('NULL');
  await page.keyboard.press('Home');
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  expect(await active()).toMatch(/-r2-c2$/);
  await page.keyboard.press('Enter');
  const detail = page.getByTestId('cell-detail');
  await expect(detail.getByRole('heading', { name: 'doc, row 3' })).toBeFocused();
  // JSON is indented plain text, never HTML (T-07).
  await expect(detail.getByTestId('cell-value')).toHaveText(
    '{\n  "n": 3,\n  "tags": [\n    "a",\n    "b"\n  ]\n}',
  );
  await page.keyboard.press('Escape');
  await expect(detail).toHaveCount(0);
  await expect(grid).toBeFocused();
  // A cut value says so in the panel; a click keeps the focus in the grid (L-7 of stu-012-review.md).
  await grid.getByRole('row').nth(2).getByRole('gridcell').nth(1).click();
  await expect(page.getByTestId('cell-cut')).toBeVisible();
  await expect(grid).toBeFocused();
});

test('Tab leaves the editor and Shift+Tab returns; the shortcuts describe the text area', async ({
  page,
}) => {
  await openEditor(page, 'select 1');
  const text = page.getByRole('textbox', { name: 'SQL of Query 1' });
  // The description is inside the editor's shadow root, so it reaches the text area (L-2).
  await expect(text).toHaveAccessibleDescription(
    /Ctrl\+(↵|Enter) runs the statement at the cursor/,
  );
  const tabKeys = () => page.evaluate(() => (window as unknown as { tabKeys: boolean[] }).tabKeys);
  await page.evaluate(() => {
    const seen: boolean[] = [];
    (window as unknown as { tabKeys: boolean[] }).tabKeys = seen;
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Tab') seen.push(event.defaultPrevented);
    });
  });
  // Without a result the editor is the page's last control: Tab is left to the browser (not prevented, no tab
  // inserted) in every browser, wherever the browser then puts the focus. Past the last control Chromium leaves for
  // its own UI, while headless Firefox has none and keeps the focus (TD-047).
  await focusEditor(page);
  await page.keyboard.press('Tab');
  expect(await tabKeys()).toEqual([false]);
  await expect(page.locator('.cm-content')).toHaveText('select 1');
  // With a result there is a control after the editor, so the focus has somewhere to go in every browser.
  await focusEditor(page);
  await page.keyboard.press('Control+Enter');
  await expect(page.getByRole('grid', { name: 'Result 1' })).toBeVisible();
  await focusEditor(page);
  await page.keyboard.press('Tab');
  await expect(page.getByTestId('export-open')).toBeFocused();
  await expect(page.locator('.cm-content')).toHaveText('select 1');
  await page.keyboard.press('Shift+Tab');
  await expect(page.locator('.cm-content')).toBeFocused();
  await expect(page.locator('.cm-content')).toHaveText('select 1');
  expect(await tabKeys()).toEqual([false, false, false]);
});

test('while connecting: a second run starts nothing, Ctrl+. and closing the tab start no query (M-2)', async ({
  page,
}) => {
  // Ctrl+Enter twice, then Ctrl+.: one connection, no query.
  await openEditor(page, 'select pg_sleep(60)', { connectDelay: 800 });
  await focusEditor(page);
  await page.keyboard.press('Control+Enter');
  await expect(page.getByTestId('result-status')).toHaveText('Connecting…');
  await expect(page.getByTestId('editor-cancel')).toBeEnabled();
  await page.keyboard.press('Control+Enter');
  await page.keyboard.press('Control+.');
  await expect(page.getByTestId('editor-notice')).toHaveText('Canceled before the query started.');
  await page.waitForTimeout(300);
  expect((await log(page)).connections).toHaveLength(1);
  expect((await log(page)).starts).toEqual([]);

  // Closing the tab while it connects.
  await openEditor(page, 'select pg_sleep(60)', { connectDelay: 800 });
  await focusEditor(page);
  await page.keyboard.press('Control+Enter');
  await expect(page.getByTestId('result-status')).toHaveText('Connecting…');
  await page.keyboard.press('Alt+w');
  await page
    .getByRole('dialog', { name: 'Script not saved' })
    .getByRole('button', { name: 'Discard' })
    .click();
  await page.waitForTimeout(1_200);
  expect((await log(page)).starts).toEqual([]);

  // A double run starts one query.
  await openEditor(page, 'select 1', { connectDelay: 800 });
  await focusEditor(page);
  await page.keyboard.press('Control+Enter');
  await page.keyboard.press('Control+Enter');
  await expect(page.getByTestId('result-status')).toHaveText('Connecting…');
  await expect(page.getByTestId('result-status')).toHaveText('3 rows · 25 ms');
  expect((await log(page)).starts).toHaveLength(1);
});

test('PROD results are cleared when the tab changes to another profile (M-1)', async ({ page }) => {
  await openEditor(page, 'select * from generate_series(1, 2000)', {
    profile: 'user:measure-central',
  });
  await page.getByTestId('editor-run').click();
  await page.getByRole('dialog').getByRole('button', { name: 'Connect' }).click();
  await expect(page.getByRole('grid')).toBeVisible();
  await expect(page.getByTestId('load-more')).toBeVisible();
  await page.getByTestId('editor-profile').selectOption('project:parts-dev');
  await expect(page.getByTestId('env-badge').last()).toHaveText(/DEV/);
  await expect(page.getByRole('grid')).toHaveCount(0);
  await expect(page.getByTestId('load-more')).toHaveCount(0);
  await expect(page.getByTestId('result-status')).toHaveText('');
});

test('parameters: a missing value stops the run and gets the focus; typed values are sent', async ({
  page,
}) => {
  await openEditor(page, 'select * from parts where id = :id');
  await focusEditor(page);
  await page.keyboard.press('Control+Enter');
  const panel = page.getByTestId('param-panel');
  const value = panel.getByRole('textbox', { name: ':id' });
  await expect(value).toBeFocused();
  await expect(panel.getByTestId('param-error')).toHaveText('Give :id a value.');
  expect((await log(page)).starts).toEqual([]);
  await panel.getByRole('combobox', { name: 'Type of :id' }).selectOption('number');
  await value.fill('12x');
  await page.getByTestId('editor-run').click();
  await expect(panel.getByTestId('param-error')).toHaveText(':id is not a number.');
  await value.fill('42');
  await page.getByTestId('editor-run').click();
  await expect(page.getByTestId('result-status')).toHaveText('3 rows · 25 ms');
  expect((await log(page)).starts[0]?.params).toEqual({ id: 42 });
  await expect(page.getByRole('grid').getByRole('columnheader').last()).toHaveText('param');
});

test('a script runs statement by statement, stops at an error, and lists every statement', async ({
  page,
}) => {
  await openEditor(
    page,
    "select * from generate_series(1, 2);\nselect 'a;b' from missing_table;\nselect 3",
  );
  await focusEditor(page);
  await page.keyboard.press('Control+Shift+Enter');
  await expect(page.getByTestId('result-status')).toHaveText('3 statements, 1 failed');
  expect((await log(page)).starts.map((s) => s.sql)).toEqual([
    'select * from generate_series(1, 2)',
    "select 'a;b' from missing_table",
  ]);
  const tabs = page.getByRole('tablist', { name: 'Results' }).getByRole('tab');
  await expect(tabs).toHaveText(['Result 1', 'Messages']);
  await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('message')).toHaveText([
    /Statement 1.*2 rows · 25 ms/,
    /Statement 2.*A table, column, function or other object does not exist\./,
    /Statement 3.*Not run/,
  ]);
  await page
    .getByTestId('message')
    .nth(1)
    .getByRole('button', { name: 'Go to statement 2' })
    .click();
  await expect(page.locator('.cm-content')).toBeFocused();
  // Arrow keys move between the result tabs.
  await tabs.nth(1).focus();
  await page.keyboard.press('ArrowLeft');
  await expect(tabs.nth(0)).toBeFocused();
  await expect(page.getByRole('grid', { name: 'Result 1' })).toBeVisible();
});

test('cancel: Ctrl+. stops a running query and says after how long', async ({ page }) => {
  await openEditor(page, 'select pg_sleep(60)');
  await focusEditor(page);
  await page.keyboard.press('Control+Enter');
  await expect(page.getByRole('progressbar', { name: 'Query running' })).toBeVisible();
  await expect(page.getByTestId('result-status')).toHaveText(/Running statement 1 of 1… \d+\.\d s/);
  await expect(page.getByTestId('editor-cancel')).toBeEnabled();
  await page.keyboard.press('Control+.');
  await expect(page.getByTestId('result-status')).toHaveText(/^Canceled after \d+\.\d s\.$/);
  expect((await log(page)).cancels).toHaveLength(1);
  await expect(page.getByRole('progressbar')).toHaveCount(0);
  await expect(page.getByTestId('editor-cancel')).toBeDisabled();
});

test('Ctrl+. while the start request is on its way cancels the query once the server answers', async ({
  page,
}) => {
  // The server has the query, the page has no id yet: the cancel must not be lost (the endless query of
  // editor-backend.spec.ts ran on when its start answered late under load).
  await openEditor(page, 'select pg_sleep(60)', { startDelay: 1_000 });
  await focusEditor(page);
  await page.keyboard.press('Control+Enter');
  await expect(page.getByRole('progressbar', { name: 'Query running' })).toBeVisible();
  expect((await log(page)).starts).toHaveLength(1);
  await page.keyboard.press('Control+.');
  expect((await log(page)).cancels).toEqual([]);
  await expect(page.getByTestId('result-status')).toHaveText(/^Canceled after \d+\.\d s\.$/);
  expect((await log(page)).cancels).toHaveLength(1);
  await expect(page.getByRole('progressbar')).toHaveCount(0);
  await expect(page.getByTestId('editor-cancel')).toBeDisabled();
});

test('Escape in the results cancels too', async ({ page }) => {
  await openEditor(page, 'select pg_sleep(60)');
  await page.getByTestId('editor-run').click();
  await page.getByTestId('editor-cancel').focus();
  await page.getByRole('tab', { name: 'Messages' }).focus();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('result-status')).toHaveText(/^Canceled after/);
});

test('a write is refused, and the page offers no write control (STU-026 locked)', async ({
  page,
}) => {
  await openEditor(page, 'delete from parts');
  await page.getByTestId('editor-run').click();
  await expect(page.getByTestId('result-status')).toHaveText(
    "Studio runs only reading statements: SELECT, WITH, VALUES, TABLE, EXPLAIN of a query, SHOW and SQLite's PRAGMA.",
  );
  await expect(page.getByTestId('access-mode')).toHaveText(/Read-only/);
  await expect(page.getByRole('button', { name: /unlock|write mode|commit/i })).toHaveCount(0);
  // The read-only refusal of the database gets the wording of 08 section 4.4.
  await page.locator('.cm-content').click();
  await page.keyboard.press('Control+A');
  await page.keyboard.type('select read_only_violation()');
  await page.getByTestId('editor-run').click();
  await expect(page.getByTestId('result-status')).toHaveText(
    'This connection is read-only. The database refused the change (read-only transaction).',
  );
  // The page never asks for a write mode.
  expect((await log(page)).starts.every((s) => !('mode' in s))).toBe(true);
});

test('a result cut at its row limit loads up to the max_rows setting', async ({ page }) => {
  await openEditor(page, 'select * from generate_series(1, 5000)');
  await page.getByTestId('editor-run').click();
  await expect(page.getByTestId('result-status')).toHaveText(
    '1,000 rows, capped at 1,000 (more rows exist) · 25 ms',
  );
  await page.getByRole('button', { name: 'Load up to 10,000 rows' }).click();
  await expect(page.getByTestId('result-status')).toHaveText('5,000 rows · 25 ms');
  expect((await log(page)).starts.map((s) => s.maxRows)).toEqual([null, 10000]);
  await expect(page.getByRole('grid')).toHaveAttribute('aria-rowcount', '5001');
});

test('tabs: Alt+T opens one, drafts survive a reload, D-06 guards a changed tab', async ({
  page,
  browserName,
}) => {
  await openEditor(page, 'select 1');
  await page.keyboard.press('Alt+t');
  const tabs = page.getByTestId('editor-tab');
  await expect(tabs).toHaveCount(2);
  await expect(tabs.nth(1)).toHaveAttribute('aria-current', 'true');
  await expect(page.locator('.cm-content')).toBeFocused();
  await page.keyboard.type('select 2');
  await expect(tabs.nth(1)).toContainText('not saved');
  await page.waitForTimeout(600);
  // A new page load of the harness without a draft of its own (a reload would leave the harness).
  await page.goto('/harness.html?scenario=editor&path=/sql');
  await expect(page.getByTestId('editor-tab')).toHaveCount(2);
  await expect(page.locator('.cm-content')).toHaveText('select 2');
  // D-06: "Cancel" has the focus; Save asks where in the project to save the new script (STU-020), and "Download
  // instead" saves the tab as a .sql download and closes it.
  await page.getByRole('button', { name: 'Close Query 2' }).click();
  const dialog = page.getByRole('dialog', { name: 'Script not saved' });
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('editor-tab')).toHaveCount(2);
  await page.keyboard.press('Alt+w');
  // The name the page offers is the link's `download` attribute; the link is gone right after its click.
  await page.evaluate(() => {
    const offered: string[] = [];
    (window as unknown as { offered: string[] }).offered = offered;
    const click = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
      if (this.hasAttribute('download')) offered.push(this.download);
      click.call(this);
    };
  });
  await dialog.getByRole('button', { name: 'Save' }).click();
  const saveDialog = page.getByRole('dialog', { name: 'Save script' });
  await expect(saveDialog.getByTestId('script-path')).toHaveValue('Query 2.sql');
  await expect(saveDialog.getByTestId('script-path')).toBeFocused();
  const downloading = page.waitForEvent('download');
  await saveDialog.getByRole('button', { name: 'Download instead' }).click();
  const file = await downloading;
  expect(await page.evaluate(() => (window as unknown as { offered: string[] }).offered)).toEqual([
    'Query 2.sql',
  ]);
  // WebKit saves it with an underscore for the space (TD-047); the other browsers keep the offered name.
  expect(file.suggestedFilename()).toBe(browserName === 'webkit' ? 'Query_2.sql' : 'Query 2.sql');
  expect(readFileSync(await file.path(), 'utf-8')).toBe('select 2');
  await expect(page.getByTestId('editor-tab')).toHaveCount(1);
  // The drafts are stored under the project's key, never the shared one; parameter values are never part of them.
  const stored = await page.evaluate(() =>
    Object.keys(localStorage).filter((key) => key.startsWith('tabrion-studio.editor-drafts')),
  );
  expect(stored).toHaveLength(1);
  expect(stored[0]).toMatch(/^tabrion-studio\.editor-drafts\.[0-9a-z]+$/);
  const text = await page.evaluate((key) => localStorage.getItem(key) ?? '', stored[0] ?? '');
  expect(text).toContain('select 1');
  expect(text).not.toContain('params');
});

test('D-02 asks before the first PROD connection; Cancel connects nothing', async ({ page }) => {
  await openEditor(page, 'select 1', { profile: 'user:measure-central' });
  await expect(page.getByTestId('env-badge').first()).toHaveText(/PROD/);
  await page.getByTestId('editor-run').click();
  const dialog = page.getByRole('dialog', { name: 'Production database' });
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  expect((await log(page)).connections).toEqual([]);
  await page.getByTestId('editor-run').click();
  await page.getByRole('dialog').getByRole('button', { name: 'Connect' }).click();
  await expect(page.getByTestId('result-status')).toHaveText('3 rows · 25 ms');
  // Once per page: the next run does not ask again.
  await page.getByTestId('editor-run').click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect((await log(page)).connections).toEqual(['user:measure-central']);
});

test('100 000 rows stay within the memory limit (03 section 10: 300 MB)', async ({
  page,
  browserName,
}) => {
  test.skip(browserName !== 'chromium', 'chromium only: the heap is measured through CDP');
  test.setTimeout(120_000);
  await openEditor(page, 'select * from generate_series(1, 100000)', { maxRows: 100_000 });
  await page.getByTestId('editor-run').click();
  await page.getByRole('button', { name: 'Load up to 100,000 rows' }).click();
  await expect(page.getByTestId('result-status')).toHaveText('100,000 rows · 25 ms');
  const grid = page.getByRole('grid');
  await expect(grid).toHaveAttribute('aria-rowcount', '100001');
  // Scroll through the whole result, then jump to its last row with the keyboard.
  for (let step = 0; step <= 20; step += 1) {
    await grid.evaluate((el, fraction) => {
      el.scrollTop = (el.scrollHeight - el.clientHeight) * fraction;
    }, step / 20);
    await expect(grid.getByRole('row').nth(1).getByRole('gridcell').first()).not.toHaveText('…');
  }
  await grid.focus();
  await page.keyboard.press('Control+End');
  await expect(grid.locator('[aria-rowindex="100001"]').getByRole('gridcell').first()).toHaveText(
    '100000',
  );
  const rows = await log(page);
  expect(Math.max(...rows.rows.map((r) => r.offset + r.limit))).toBe(100_000);
  // Only what is visible is in the DOM.
  expect(await grid.getByRole('row').count()).toBeLessThan(120);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('HeapProfiler.collectGarbage');
  await cdp.send('Performance.enable');
  const { metrics } = await cdp.send('Performance.getMetrics');
  const heap = metrics.find((m) => m.name === 'JSHeapUsedSize')?.value ?? Infinity;
  const total = metrics.find((m) => m.name === 'JSHeapTotalSize')?.value ?? Infinity;
  console.warn(
    `100 000 rows: JS heap used ${(heap / 2 ** 20).toFixed(1)} MB, total ${(total / 2 ** 20).toFixed(1)} MB`,
  );
  expect(total).toBeLessThan(300 * 2 ** 20);
  // The grid's own cache holds at most 10 000 rows; the heap stays far below the limit.
  expect(heap).toBeLessThan(100 * 2 ** 20);
});

test('wide rows stay within the memory limit too: the cache is bounded in bytes (M-4)', async ({
  page,
  browserName,
}) => {
  test.skip(browserName !== 'chromium', 'chromium only: the heap is measured through CDP');
  test.setTimeout(120_000);
  // 1 000 rows of three texts of 30 000 characters "€" (2 bytes each in V8): about 180 MB if all were held.
  await openEditor(page, 'select * from generate_series(1, 1000) wide(30000)');
  await page.getByTestId('editor-run').click();
  await expect(page.getByTestId('result-status')).toHaveText('1,000 rows · 25 ms');
  const grid = page.getByRole('grid');
  for (let step = 0; step <= 40; step += 1) {
    await grid.evaluate((el, fraction) => {
      el.scrollTop = (el.scrollHeight - el.clientHeight) * fraction;
    }, step / 40);
    await expect(grid.getByRole('row').nth(1).getByRole('gridcell').first()).not.toHaveText('…');
  }
  const offsets = (await log(page)).rows;
  expect(Math.max(...offsets.map((r) => r.offset + r.limit))).toBe(1_000);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('HeapProfiler.collectGarbage');
  await cdp.send('Performance.enable');
  const { metrics } = await cdp.send('Performance.getMetrics');
  const heap = metrics.find((m) => m.name === 'JSHeapUsedSize')?.value ?? Infinity;
  const total = metrics.find((m) => m.name === 'JSHeapTotalSize')?.value ?? Infinity;
  console.warn(
    `1 000 wide rows: JS heap used ${(heap / 2 ** 20).toFixed(1)} MB, total ${(total / 2 ** 20).toFixed(1)} MB`,
  );
  expect(total).toBeLessThan(300 * 2 ** 20);
  // The byte budget is 64 MB; without it the heap would hold about 180 MB of text.
  expect(heap).toBeLessThan(140 * 2 ** 20);
});

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as const) {
    test(`axe: no serious or critical violation (${theme}, ${lang})`, async ({ page }) => {
      await openEditor(
        page,
        'select * from parts where material = :material;\nselect * from generate_series(1, 3)',
        {
          theme,
          lang,
        },
      );
      expect(await seriousViolations(page)).toEqual([]);
      await page.getByTestId('param-panel').getByRole('textbox').fill('Aluminium');
      await page.getByTestId('editor-run-script').click();
      await expect(page.getByRole('grid')).toBeVisible();
      await page.getByRole('grid').getByRole('gridcell').first().click();
      await expect(page.getByTestId('cell-detail')).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
      await page.getByRole('tab').last().click();
      await expect(page.getByTestId('messages')).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
      // The shortcut help lists the editor's shortcuts.
      await page.getByTestId('open-help').click();
      await expect(page.getByTestId('shortcut-help')).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
    });
  }
}

test('opens from the activity bar and the palette', async ({ page }) => {
  await openHarness(page, { scenario: 'editor' });
  await page.getByRole('link', { name: 'SQL editor' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'SQL editor' })).toBeVisible();
  await expect(page).toHaveTitle('SQL editor – Tabrion Studio');
  await page.keyboard.press('Control+k');
  await page.keyboard.type('run script');
  await expect(page.getByRole('option', { name: /Run script/ })).toBeVisible();
});
