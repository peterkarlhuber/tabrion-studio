/**
 * STU-008: the connection manager in Chromium. The list, each step of the "New connection" assistant, the delete
 * confirmation and the password dialog have no serious or critical axe violation (colour contrast included) in light
 * and dark, English and German; every write sends Content-Type: application/json, DELETE included.
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
    });
  }
}

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
