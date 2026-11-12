/**
 * STU-008: the connection manager in Chromium. The list, each step of the "New connection" assistant, the delete
 * confirmation, the password dialog, a test result, a failed test, D-02 and the connected PROD shell have no serious
 * or critical axe violation (colour contrast included) in light and dark, English and German; every write sends
 * Content-Type: application/json, DELETE included. The flow of 08 section 5 "Connect": test, D-02 before a PROD
 * connection (once per session), the header, the PROD frame and the window title, disconnect.
 */
import { test, expect, type Page } from '@playwright/test';

import { openHarness, seriousViolations, type Theme } from './support';

async function writes(page: Page) {
  return page.evaluate(
    () =>
      (window as unknown as { writes: { method: string; url: string; contentType: string }[] })
        .writes,
  );
}

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as const) {
    test(`${theme}, ${lang}: list, assistant and dialogs pass axe`, async ({ page }) => {
      await openHarness(page, { scenario: 'connections', path: '/connections', theme, lang });
      await expect(page.getByTestId('connection-item')).toHaveCount(3);
      expect(await seriousViolations(page)).toEqual([]);
      await page.getByTestId('connection-item').nth(2).click();
      await expect(page.getByTestId('connection-detail')).toContainText('measure-central');
      expect(await seriousViolations(page)).toEqual([]);
      for (const step of [1, 2, 3]) {
        if (step === 1) {
          await page
            .getByRole('button', { name: lang === 'en' ? 'New connection' : 'Neue Verbindung' })
            .click();
          await page.getByRole('radio', { name: 'PostgreSQL' }).check();
          await page.getByRole('dialog').getByLabel('Host').fill('db.internal');
          await page
            .getByRole('dialog')
            .getByLabel(lang === 'en' ? 'Database' : 'Datenbank')
            .fill('m');
        }
        await expect(page.getByTestId(`wizard-step-${step}`)).toBeVisible();
        expect(await seriousViolations(page)).toEqual([]);
        if (step < 3) {
          await page.getByRole('button', { name: lang === 'en' ? 'Next' : 'Weiter' }).click();
        }
      }
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toHaveCount(0);
      // The connection test of a PROD profile: D-02 first (C-2 of the connect review), then a result with its
      // facts; then a failure with its remedy.
      const detail = page.getByTestId('connection-detail');
      await detail
        .getByRole('button', { name: lang === 'en' ? 'Test connection' : 'Verbindung testen' })
        .click();
      const prod = page.getByRole('dialog', {
        name: lang === 'en' ? 'Production database' : 'Produktionsdatenbank',
      });
      await expect(prod).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
      await prod
        .getByRole('button', { name: lang === 'en' ? 'Connect' : 'Verbinden', exact: true })
        .click();
      await expect(detail.getByTestId('connection-outcome')).toContainText(
        lang === 'en' ? 'PostgreSQL 17.6' : 'PostgreSQL-Version 17.6',
      );
      expect(await seriousViolations(page)).toEqual([]);
      await page.getByTestId('connection-item').nth(1).click();
      await detail
        .getByRole('button', { name: lang === 'en' ? 'Test connection' : 'Verbindung testen' })
        .click();
      await expect(detail.getByTestId('connection-outcome')).toContainText(
        lang === 'en' ? 'Test failed' : 'Test fehlgeschlagen',
      );
      expect(await seriousViolations(page)).toEqual([]);
      // The connected PROD shell (D-02 was confirmed for the test above).
      await page.getByTestId('connection-item').nth(2).click();
      await detail
        .getByRole('button', { name: lang === 'en' ? 'Connect' : 'Verbinden', exact: true })
        .click();
      await expect(page.getByRole('dialog')).toHaveCount(0);
      await expect(page.getByTestId('connection-switcher')).toContainText('measure-central');
      await expect(page.locator('[data-env="prod"]').first()).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
    });
  }
}

test('08 section 5 "Connect": test, D-02 once per session, header, PROD frame, disconnect', async ({
  page,
}) => {
  await openHarness(page, { scenario: 'connections', path: '/connections' });
  const detail = page.getByTestId('connection-detail');
  // DEV: no dialog.
  await page.getByTestId('connection-item').nth(0).click();
  await detail.getByRole('button', { name: 'Test connection' }).click();
  await expect(detail.getByTestId('connection-outcome')).toContainText(
    'Connection works: 0.8 ms · SQLite 3.45.1 · foreign keys on · journal wal · encoding UTF-8',
  );
  await detail.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.getByTestId('connection-switcher')).toContainText('parts-dev');
  await expect(page.getByTestId('connection-switcher')).toContainText('connected');
  await expect(page).toHaveTitle('[DEV] parts-dev – Tabrion Studio');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  // A failed test names the remedy, the header keeps its connection.
  await page.getByTestId('connection-item').nth(1).click();
  await detail.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(detail.getByTestId('connection-outcome')).toContainText(
    'Not connected: Studio could not connect. Check the host and the port',
  );
  await expect(page.getByTestId('connection-switcher')).toContainText('parts-dev');
  // PROD: D-02 first, default Cancel, Escape cancels.
  await page.getByTestId('connection-item').nth(2).click();
  await detail.getByRole('button', { name: 'Connect', exact: true }).click();
  let prod = page.getByRole('dialog', { name: 'Production database' });
  await expect(prod.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page).toHaveTitle('[DEV] parts-dev – Tabrion Studio');
  // L-1 of the connect review: the focus goes back to the button that asked.
  await expect(detail.getByRole('button', { name: 'Connect', exact: true })).toBeFocused();
  await detail.getByRole('button', { name: 'Connect', exact: true }).click();
  prod = page.getByRole('dialog', { name: 'Production database' });
  await prod.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page).toHaveTitle('[PROD] measure-central – Tabrion Studio');
  await expect(detail.getByRole('button', { name: 'Use this connection' })).toBeFocused();
  await expect(page.locator('[data-env="prod"]').first()).toBeVisible();
  await expect(page.getByTestId('access-mode')).toHaveText(/Writing locked/);
  await expect(detail.getByTestId('connection-state')).toHaveText('open (read-only)');
  // Disconnect, connect again: no second D-02 in this session.
  await detail.getByRole('button', { name: 'Disconnect' }).click();
  await expect(page.getByRole('button', { name: /No connection/ })).toBeVisible();
  await expect(detail.getByRole('button', { name: 'Connect', exact: true })).toBeFocused();
  await detail.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page).toHaveTitle('[PROD] measure-central – Tabrion Studio');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  // The test of a confirmed PROD profile does not ask again.
  await detail.getByRole('button', { name: 'Test connection' }).click();
  await expect(detail.getByTestId('connection-outcome')).toContainText('PostgreSQL 17.6');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  const sent = await writes(page);
  expect(sent.map((w) => `${w.method} ${w.url}`)).toEqual([
    'POST /api/connection-tests',
    'POST /api/connections',
    'POST /api/connections',
    'POST /api/connections',
    'DELETE /api/connections',
    'POST /api/connections',
    'POST /api/connection-tests',
  ]);
  for (const write of sent) expect(write.contentType).toBe('application/json');
});

test('TD-038: a profile another tab made PROD is refused by the server, read again and asked D-02', async ({
  page,
}) => {
  await openHarness(page, { scenario: 'connections', path: '/connections' });
  const detail = page.getByTestId('connection-detail');
  await page.getByTestId('connection-item').nth(0).click();
  await expect(detail).toContainText('parts-dev');
  await page.evaluate(() =>
    (window as unknown as { harnessMakeProd: (id: string) => void }).harnessMakeProd(
      'project:parts-dev',
    ),
  );
  await detail.getByRole('button', { name: 'Connect', exact: true }).click();
  const prod = page.getByRole('dialog', { name: 'Production database' });
  await expect(prod).toContainText('parts-dev is marked as PROD');
  await expect(prod.getByRole('button', { name: 'Cancel' })).toBeFocused();
  expect(await seriousViolations(page)).toEqual([]);
  await prod.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page).toHaveTitle('[PROD] parts-dev – Tabrion Studio');
  const sent = await writes(page);
  expect(sent.map((w) => `${w.method} ${w.url}`)).toEqual([
    'POST /api/connections',
    'POST /api/connections',
  ]);
});

test('create, delete and password: every write is JSON', async ({ page }) => {
  await openHarness(page, { scenario: 'connections', path: '/connections' });
  await page.getByRole('button', { name: 'New connection' }).click();
  const dialog = page.getByRole('dialog', { name: 'New connection' });
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await dialog.getByRole('textbox', { name: 'File' }).fill('/data/new.db');
  await dialog.getByRole('button', { name: 'Next' }).click();
  await dialog.getByRole('button', { name: 'Next' }).click();
  await dialog.getByLabel('Name').fill('new');
  await dialog.getByRole('radio', { name: 'DEV' }).check();
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByTestId('connection-item').nth(2).click();
  await page.getByRole('button', { name: 'Delete' }).click();
  const confirm = page.getByRole('dialog', { name: 'Delete measure-central?' });
  await expect(confirm.getByRole('button', { name: 'Cancel' })).toBeFocused();
  expect(await seriousViolations(page)).toEqual([]);
  await confirm.getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByTestId('connection-item').nth(2).click();
  await page.getByRole('button', { name: 'Enter password' }).click();
  const password = page.getByRole('dialog', { name: 'Password for measure-central' });
  expect(await seriousViolations(page)).toEqual([]);
  await password.getByLabel('Password').fill('typed');
  await password.getByRole('button', { name: 'Save password' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', { name: 'Remove saved password' }).click();
  await expect.poll(async () => (await writes(page)).length).toBe(4);
  const sent = await writes(page);
  expect(sent.map((w) => w.method)).toEqual(['POST', 'DELETE', 'PUT', 'DELETE']);
  for (const write of sent) expect(write.contentType).toBe('application/json');
});

test('settings: queries and security pass axe', async ({ page }) => {
  await openHarness(page, { scenario: 'connections', path: '/settings', theme: 'dark' });
  await expect(page.getByLabel('Maximum rows per result')).toHaveValue('10000');
  expect(await seriousViolations(page)).toEqual([]);
});
