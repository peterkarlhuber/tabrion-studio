/**
 * STU-020 in the e2e harness: the query history, favorite and recently opened profiles, `.sql` scripts of the
 * project, "Format" and completion from the explorer's catalog in the SQL editor. Keyboard flows and axe (light and
 * dark, English and German). The same flows against a real server: e2e/history-backend.spec.ts.
 */
import { test, expect, type Page } from '@playwright/test';

import { openHarness, seriousViolations, type Theme } from './support';

interface HistoryLog {
  deletes: Record<string, unknown>[];
  favoritePuts: Record<string, unknown>[];
  scriptPuts: Record<string, unknown>[];
  catalogs: string[];
  tables: string[];
}

function historyLog(page: Page): Promise<HistoryLog> {
  return page.evaluate(() => (window as unknown as { historyLog: HistoryLog }).historyLog);
}

async function openEditor(page: Page, sql: string, profile?: string): Promise<void> {
  const params = new URLSearchParams({ scenario: 'editor', path: '/sql', sql });
  if (profile) params.set('profile', profile);
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
  await page.goto(`/harness.html?${params.toString()}`);
  await expect(page.getByTestId('backend-status')).toContainText('0.1.0.dev0');
  await expect(page.locator('.cm-content')).toBeVisible();
}

test('history: the statements with their parameter names; "Open in editor" opens a tab that runs nothing', async ({
  page,
}) => {
  await openHarness(page, { scenario: 'editor', path: '/history' });
  await expect(page.getByRole('heading', { level: 1, name: 'Query history' })).toBeVisible();
  const entries = page.getByTestId('history-entry');
  await expect(entries).toHaveCount(3);
  await expect(entries.first().getByTestId('history-sql')).toHaveText(
    'select * from parts where id = :id',
  );
  await expect(entries.first().getByTestId('history-params')).toHaveText(
    'Parameters (values not kept): :id',
  );
  // The PROD entry carries its badge.
  await expect(entries.nth(1)).toContainText('PROD');
  await page.getByTestId('history-search').fill('count');
  await expect(entries).toHaveCount(1);
  await page.getByTestId('history-search').fill('');
  await expect(entries).toHaveCount(3);
  await entries.first().getByTestId('history-open').click();
  await expect(page).toHaveURL(/\/sql$/);
  await expect(page.getByTestId('editor-tab').last()).toContainText('From history');
  await expect(page.locator('.cm-content')).toHaveText('select * from parts where id = :id');
  // The value is asked again; nothing ran.
  await expect(page.getByTestId('param-panel').getByRole('textbox', { name: ':id' })).toHaveValue(
    '',
  );
  const queries = await page.evaluate(
    () => (window as unknown as { queryLog: { starts: unknown[] } }).queryLog.starts,
  );
  expect(queries).toEqual([]);
});

test('keyboard: Ctrl+H opens the history, Tab reaches its controls, the delete dialog keeps the safe focus', async ({
  page,
}) => {
  await openHarness(page, { scenario: 'editor', path: '/' });
  await page.keyboard.press('Control+h');
  await expect(page).toHaveURL(/\/history$/);
  await expect(page.getByRole('main')).toBeFocused();
  await expect(page.getByTestId('history-entry')).toHaveCount(3);
  await page.getByTestId('history-search').focus();
  await page.keyboard.press('Shift+Tab');
  await expect(page.getByTestId('history-profile')).toBeFocused();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await expect(page.getByTestId('history-delete-all')).toBeFocused();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog', { name: 'Delete the whole history?' });
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId('history-delete-all')).toBeFocused();
  expect((await historyLog(page)).deletes).toEqual([]);
  // The palette offers the page with its keys.
  await page.keyboard.press('Control+k');
  await page.keyboard.type('history');
  await expect(page.getByRole('option', { name: /Go to Query history/ })).toContainText('Ctrl+H');
});

test('favorites: the star in the connection manager, the start page lists it, Connect goes through the manager', async ({
  page,
}) => {
  await openHarness(page, {
    scenario: 'connections',
    path: '/connections',
    recent: 'project:parts-dev',
  });
  const star = page.getByTestId('connection-favorite');
  await expect(star).toHaveAttribute('aria-pressed', 'false');
  await star.focus();
  await page.keyboard.press('Enter');
  await expect(star).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByText('parts-dev is a favorite now.')).toBeVisible();
  expect((await historyLog(page)).favoritePuts).toEqual([
    { profile_id: 'project:parts-dev', favorite: true },
  ]);
  await page.getByRole('link', { name: 'Start' }).click();
  const favorites = page.getByTestId('start-favorites');
  await expect(favorites).toContainText('parts-dev');
  await expect(page.getByTestId('start-recent')).toContainText('parts-dev');
  await favorites.getByTestId('quick-connect').click();
  await expect(page).toHaveURL(/\/connections$/);
  await expect(page.getByTestId('connection-detail')).toContainText('parts-dev');
  await expect(page.getByTestId('connection-state')).toHaveText('open (read-only)');
});

test('scripts: open one of the project, change it and save it back with Ctrl+S; a new one asks for its path', async ({
  page,
}) => {
  await openEditor(page, 'select 2');
  await page.getByTestId('editor-open').click();
  const dialog = page.getByRole('dialog', { name: 'Open script' });
  await expect(dialog.getByTestId('script-filter')).toBeFocused();
  await page.keyboard.type('report');
  await expect(dialog.getByTestId('script-item')).toHaveCount(1);
  await page.keyboard.press('Tab');
  await expect(dialog.getByTestId('script-item')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('editor-tab').last()).toContainText('report');
  await expect(page.locator('.cm-content')).toContainText('where id = :id;');
  await page.locator('.cm-content').click();
  await page.keyboard.press('Control+End');
  await page.keyboard.insertText('-- checked');
  await expect(page.getByTestId('editor-tab').last()).toContainText('not saved');
  await page.keyboard.press('Control+s');
  await expect(page.getByTestId('editor-notice')).toHaveText('Saved to sql/report.sql.');
  await expect(page.getByTestId('editor-tab').last()).not.toContainText('not saved');
  const saved = (await historyLog(page)).scriptPuts;
  expect(saved).toHaveLength(1);
  expect(saved[0]).toMatchObject({ path: 'sql/report.sql', overwrite: false });
  expect(typeof saved[0]?.digest).toBe('string');

  // A tab without a script: Ctrl+S asks for a path in the project.
  await page.getByTestId('editor-tab').first().click();
  await page.locator('.cm-content').click();
  await page.keyboard.press('Control+s');
  const save = page.getByRole('dialog', { name: 'Save script' });
  await expect(save.getByTestId('script-path')).toBeFocused();
  await page.keyboard.press('Control+a');
  await page.keyboard.type('sql/new.sql');
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('editor-notice')).toHaveText('Saved to sql/new.sql.');
  await expect(page.getByTestId('editor-tab').first()).toContainText('new');
});

test('format: Alt+Shift+F formats the text with :name kept; Ctrl+Z takes it back', async ({
  page,
}) => {
  await openEditor(page, 'select id,label from parts where id=:id');
  await page.locator('.cm-content').click();
  await page.keyboard.press('Alt+Shift+F');
  await expect(page.getByTestId('editor-notice')).toContainText('Formatted.');
  await expect(page.locator('.cm-line')).toHaveText([
    'SELECT',
    '  id,',
    '  label',
    'FROM',
    '  parts',
    'WHERE',
    '  id = :id',
  ]);
  await page.keyboard.press('Control+z');
  await expect(page.locator('.cm-content')).toHaveText('select id,label from parts where id=:id');
});

test('completion: table names from the catalog once connected, columns after "parts."', async ({
  page,
}) => {
  await openEditor(page, 'select 1');
  // No connection yet: the page reads no catalog.
  expect((await historyLog(page)).catalogs).toEqual([]);
  await page.getByTestId('editor-run').click();
  await expect(page.getByTestId('result-status')).not.toBeEmpty();
  await expect.poll(async () => (await historyLog(page)).catalogs.length).toBe(1);
  await page.locator('.cm-content').click();
  await page.keyboard.press('Control+End');
  await page.keyboard.press('Enter');
  await page.keyboard.type('select * from supp');
  const list = page.locator('.cm-tooltip-autocomplete');
  await expect(list.locator('li[aria-selected]')).toContainText('suppliers');
  // CodeMirror takes no key for 75 ms after the list opened (its interactionDelay), so a list is not picked unseen.
  await page.waitForTimeout(100);
  await page.keyboard.press('Enter');
  await expect(page.locator('.cm-line').last()).toHaveText('select * from suppliers');
  await page.keyboard.press('Enter');
  await page.keyboard.type('select parts.');
  await expect
    .poll(async () => (await historyLog(page)).tables)
    .toEqual(['/api/connections/c-project%3Aparts-dev/table?schema=main&name=parts']);
  await expect(list).toContainText('label');
  await expect(list).toContainText('weight');
  await page.keyboard.press('Escape');
});

test('on PROD the editor never reads a catalog by itself; "Load names" does after D-02', async ({
  page,
}) => {
  await openEditor(page, 'select 1', 'user:measure-central');
  await page.getByTestId('editor-run').click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: /Connect/ })
    .click();
  const load = page.getByTestId('editor-load-names');
  await expect(load).toBeVisible();
  expect((await historyLog(page)).catalogs).toEqual([
    '/api/connections/c-user%3Ameasure-central/catalog?cached_only=true',
  ]);
  await load.click();
  await expect(page.getByTestId('editor-notice')).toHaveText(
    'Table and view names loaded for completion.',
  );
  expect((await historyLog(page)).catalogs).toHaveLength(2);
});

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as const) {
    test(`axe, ${theme}, ${lang}: history, start page with favorites and the script dialogs`, async ({
      page,
    }) => {
      await openHarness(page, { scenario: 'editor', path: '/history', theme, lang });
      await expect(page.getByTestId('history-entry')).toHaveCount(3);
      expect(await seriousViolations(page)).toEqual([]);
      await openHarness(page, {
        scenario: 'connections',
        path: '/',
        theme,
        lang,
        favorites: 'project:parts-dev',
        recent: 'user:measure-central',
      });
      await expect(page.getByTestId('start-favorites')).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
      await openHarness(page, { scenario: 'editor', path: '/sql', theme, lang });
      await page.getByTestId('editor-open').click();
      await expect(page.getByTestId('script-item')).toHaveCount(2);
      expect(await seriousViolations(page)).toEqual([]);
      await page.keyboard.press('Escape');
      await page.getByTestId('editor-save').click();
      await expect(page.getByTestId('script-path')).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
    });
  }
}
