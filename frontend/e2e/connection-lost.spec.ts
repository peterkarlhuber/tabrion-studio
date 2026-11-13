/**
 * TD-034, 08 section 5 "error recovery", in Chromium against the e2e harness: the banner "Connection lost" with
 * "Reconnect" when the page's connection check or a request finds the database gone (`window.harnessLose(id)` of
 * src/harness/main.tsx), axe in light and dark, English and German, "Reconnect" by keyboard, D-02 again for PROD, and
 * the SQL editor's query on a lost connection. The real server's flows are in e2e/connection-lost-backend.spec.ts.
 */
import { test, expect, type Page } from '@playwright/test';

import { openHarness, seriousViolations, type Theme } from './support';

async function lose(page: Page, connectionId: string): Promise<void> {
  await page.evaluate((id) => {
    (window as unknown as { harnessLose: (id: string) => void }).harnessLose(id);
  }, connectionId);
}

async function writes(page: Page) {
  return page.evaluate(
    () => (window as unknown as { writes: { method: string; url: string }[] }).writes,
  );
}

const TEXT = {
  en: { title: 'Connection lost: parts-dev', reconnect: 'Reconnect' },
  de: { title: 'Verbindung getrennt: parts-dev', reconnect: 'Neu verbinden' },
} as const;

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as const) {
    test(`the connection check finds the database gone: banner, axe (${theme}, ${lang})`, async ({
      page,
    }) => {
      await openHarness(page, {
        scenario: 'explorer',
        path: '/explorer',
        theme,
        lang,
        check: '200',
      });
      await expect(page.getByTestId('explorer-open')).toBeEnabled();
      await page.getByTestId('explorer-open').click();
      await expect(page.getByRole('tree')).toBeVisible();
      await lose(page, 'c-sqlite');
      const banner = page.getByTestId('connection-lost');
      await expect(banner).toBeVisible();
      await expect(banner).toHaveAttribute('role', 'alert');
      await expect(banner).toContainText(TEXT[lang].title);
      // The banner is inside the main area, above the view, and passes axe with colour contrast.
      await expect(page.getByRole('main').getByTestId('connection-lost')).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);

      // "Reconnect" by keyboard: the banner goes, the focus moves to the main area and the tree is read again.
      const button = banner.getByRole('button', { name: TEXT[lang].reconnect });
      await button.focus();
      await page.keyboard.press('Enter');
      await expect(page.getByTestId('connection-lost')).toHaveCount(0);
      await expect(page.getByRole('main')).toBeFocused();
      await expect(page.getByRole('tree')).toBeVisible();
    });
  }
}

test('a request finds a PROD database gone: Reconnect asks D-02 again', async ({ page }) => {
  await openHarness(page, { scenario: 'explorer', path: '/explorer' });
  await page.getByRole('combobox').first().selectOption('user:measure-central');
  await page.getByTestId('explorer-open').click();
  let dialog = page.getByRole('dialog', { name: 'Production database' });
  await dialog.getByRole('button', { name: 'Connect' }).click();
  await expect(page.getByRole('treeitem', { name: 'public (6)' })).toBeVisible();

  await lose(page, 'c-pg');
  await page.getByTestId('explorer-refresh').click();
  const banner = page.getByTestId('connection-lost');
  await expect(banner).toContainText('Connection lost: measure-central');
  await expect(banner.getByTestId('env-badge')).toHaveAttribute('data-env', 'prod');
  expect(await seriousViolations(page)).toEqual([]);

  // The lost connection's confirmation is forgotten: D-02 again, and Cancel connects nothing.
  await banner.getByRole('button', { name: 'Reconnect' }).click();
  dialog = page.getByRole('dialog', { name: 'Production database' });
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toHaveCount(0);
  await expect(banner).toBeVisible();
  const opens = (await writes(page)).filter((w) => w.url === '/api/connections');
  expect(opens).toHaveLength(1);

  await banner.getByRole('button', { name: 'Reconnect' }).click();
  await page
    .getByRole('dialog', { name: 'Production database' })
    .getByRole('button', { name: 'Connect' })
    .click();
  await expect(page.getByTestId('connection-lost')).toHaveCount(0);
  await expect(page.getByRole('treeitem', { name: 'public (6)' })).toBeVisible();
  expect((await writes(page)).filter((w) => w.url === '/api/connections')).toHaveLength(2);
});

test('the SQL editor: a query on a lost connection shows the banner; Reconnect, then run again', async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
  await page.goto(
    `/harness.html?scenario=editor&path=/sql&${new URLSearchParams({ sql: 'select connection_lost()' }).toString()}`,
  );
  await expect(page.getByTestId('backend-status')).toContainText('0.1.0.dev0');
  await page.locator('.cm-content').click();
  await page.keyboard.press('Control+Enter');
  const banner = page.getByTestId('connection-lost');
  await expect(banner).toContainText('Connection lost: parts-dev');
  await banner.getByRole('button', { name: 'Reconnect' }).click();
  await expect(page.getByTestId('connection-lost')).toHaveCount(0);

  await page.locator('.cm-content').click();
  await page.keyboard.press('Control+A');
  await page.keyboard.type('select * from generate_series(1, 3)');
  await page.keyboard.press('Control+Enter');
  await expect(page.getByTestId('result-status')).toHaveText('3 rows · 25 ms');
});
