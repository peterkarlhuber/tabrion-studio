/**
 * QA-S1 sections I and J: "Verbindung getrennt" after "Trennen" in the same tab, and stopping and starting Studio
 * again with the kit's commands (the tab says the session ended; drafts and the language are kept).
 */
import type { Page } from '@playwright/test';

import {
  chooseProfile,
  expect,
  goTo,
  openEditor,
  replaceText,
  signIn,
  skipWithoutStudio,
  startSession,
  test,
} from './studio';

skipWithoutStudio();

const DEV = 'Shop (DEV)';

/** A GET of the API from the signed-in page, with its CSRF header (setup checks only). */
async function api(page: Page, path: string): Promise<string> {
  return page.evaluate(async (path) => {
    const csrf = sessionStorage.getItem('tabrion-studio.csrf') ?? '';
    return (await fetch(path, { headers: { 'X-Tabrion-CSRF': csrf } })).text();
  }, path);
}

test('S1-32 after "Trennen" in the same tab, "Ausführen" in the SQL editor shows "Verbindung getrennt" with "Neu verbinden"', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await openEditor(page, DEV);
  const sql = 'select count(*) as n from orders';
  await replaceText(page, sql);
  await page.getByRole('button', { name: 'Ausführen', exact: true }).click();
  await expect(page.getByTestId('result-status')).toHaveText(/^1 Zeile/);

  // In the same browser tab: "Verbindungen" → "Shop (DEV)" → "Trennen" (checklist v2: a second start would replace
  // the session, so the checklist no longer uses a second tab).
  await goTo(page, 'Verbindungen');
  await page.getByTestId('connection-item').filter({ hasText: DEV }).click();
  await page
    .getByTestId('connection-detail')
    .getByRole('button', { name: 'Trennen', exact: true })
    .click();
  await expect(
    page.getByTestId('connection-detail').getByRole('button', { name: 'Verbinden', exact: true }),
  ).toBeVisible();

  // Back to the SQL editor, with the query still open, and "Ausführen" again.
  await goTo(page, 'SQL-Editor');
  await expect(page.locator('.cm-content')).toHaveText(sql);
  await page.getByRole('button', { name: 'Ausführen', exact: true }).click();
  const banner = page.getByTestId('connection-lost');
  await expect(banner).toContainText('Verbindung getrennt: Shop (DEV)');
  await banner.getByRole('button', { name: 'Neu verbinden', exact: true }).click();
  await expect(banner).toHaveCount(0);
  // Nothing was lost: the tab's text is there and runs again.
  await expect(page.locator('.cm-content')).toHaveText(sql);
  await page.getByRole('button', { name: 'Ausführen', exact: true }).click();
  await expect(page.getByTestId('result-status')).toHaveText(/^1 Zeile/);
});

test('S1-33 after "3 - Studio beenden" (tabrion-studio --stop), the open tab says the session ended or the server does not answer', async ({
  page,
  studio,
}) => {
  // The page learns that the server is gone from its heartbeat, sent every 60 s (HEARTBEAT_MS in
  // src/heartbeat.ts), so the tab may take up to a minute to say so: the wait follows that interval.
  test.setTimeout(120_000);
  await startSession(page, studio);
  await goTo(page, 'SQL-Editor');
  await studio.stopLikeTheKit();
  const stopped = Date.now();
  await expect(
    page
      .getByText('Deine Sitzung wurde beendet.', { exact: false })
      .or(page.getByText('Der lokale Server antwortet nicht.', { exact: false })),
  ).toBeVisible({ timeout: 65_000 });
  test.info().annotations.push({
    type: 'seconds until the tab said so',
    description: String((Date.now() - stopped) / 1000),
  });
});

test('S1-34 after a restart, the editor tabs are back without parameter values and the language is kept', async ({
  page,
  studio,
  browser,
}) => {
  await startSession(page, studio);
  // The language chosen in the settings (kept in Studio's settings, not in the browser).
  // German comes from the browser at first; choosing it in the settings (via English) stores it in Studio.
  await goTo(page, 'Einstellungen');
  await page.getByRole('radio', { name: 'English', exact: true }).check();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await page.getByRole('radio', { name: 'Deutsch', exact: true }).check();
  await expect(page.locator('html')).toHaveAttribute('lang', 'de');
  await expect.poll(() => api(page, '/api/settings')).toMatch(/"language":\s*"de"/);
  await openEditor(page, DEV);
  const sql = 'select order_id from orders where status = :status';
  await replaceText(page, sql);
  await page.getByRole('button', { name: 'Ausführen', exact: true }).click();
  await page.getByTestId('param-panel').getByRole('textbox', { name: ':status' }).fill('paid');
  await page.getByRole('button', { name: 'Ausführen', exact: true }).click();
  await expect(page.getByTestId('result-status')).toHaveText(/Zeilen/);
  // The draft is saved on the server before Studio stops.
  await expect.poll(() => api(page, '/api/drafts')).toContain(':status');
  await studio.stopLikeTheKit();
  await studio.start();

  // A browser with English preferences: the page is German only if the setting was kept.
  const context = await browser.newContext({ locale: 'en-US' });
  try {
    const again = await context.newPage();
    await signIn(again, studio.link());
    await expect(again.locator('html')).toHaveAttribute('lang', 'de');
    await goTo(again, 'SQL-Editor');
    await expect(again.locator('.cm-content')).toHaveText(sql);
    await again.getByRole('button', { name: 'Ausführen', exact: true }).click();
    await expect(
      again.getByTestId('param-panel').getByRole('textbox', { name: ':status' }),
    ).toHaveValue('');
    await expect(again.getByTestId('param-panel')).toContainText('Gib :status einen Wert.');
  } finally {
    await context.close();
  }
});

test('S1-38 a restart gets a new address; the old tab, reloaded, shows no Studio data; the new tab works', async ({
  page,
  studio,
}) => {
  // The old tab (as left over from S1-34): signed in to the first start.
  await startSession(page, studio);
  await goTo(page, 'Explorer');
  const oldOrigin = studio.origin;
  await studio.stopLikeTheKit();
  await studio.start();
  // "2 - Studio starten" again: the new tab, in the same browser.
  const fresh = await page.context().newPage();
  await signIn(fresh, studio.link());
  const newOrigin = new URL(fresh.url()).origin;
  expect(newOrigin).not.toBe(oldOrigin);
  expect(newOrigin).toMatch(/^http:\/\/127(\.\d{1,3}){3}:\d+$/);
  // The old tab, reloaded: the browser's error page (nothing listens there any more) or "Nicht angemeldet".
  await page.bringToFront();
  let unreachable = false;
  try {
    await page.reload();
  } catch (error) {
    unreachable = /ERR_CONNECTION_REFUSED|ERR_ADDRESS_UNREACHABLE|ERR_CONNECTION_RESET/.test(
      String(error),
    );
    if (!unreachable) throw error;
  }
  if (!unreachable) {
    await expect(page.getByTestId('session-status')).toContainText('Nicht angemeldet.');
  }
  await expect(page.getByRole('tree')).toHaveCount(0);
  await expect(page.getByTestId('explorer-workspace')).toHaveCount(0);
  // The new tab works normally.
  await fresh.bringToFront();
  await goTo(fresh, 'Explorer');
  await chooseProfile(fresh, 'Verbindungsprofil', 'Shop (DEV)');
  await fresh.getByRole('button', { name: 'Öffnen', exact: true }).click();
  await expect(fresh.getByRole('treeitem', { name: 'orders', exact: true })).toBeVisible();
});
