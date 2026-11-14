/**
 * QA-S1 section E, "PROD-Warnungen und Bestätigungen": the question D-02 before a PROD connection, the PROD marking
 * and "Schreiben gesperrt", the refused write on PROD, and D-02 again after a reload.
 */
import { createHash } from 'node:crypto';

import type { Page } from '@playwright/test';

import {
  chooseProfile,
  expect,
  goTo,
  openEditor,
  prodDialog,
  settled,
  skipWithoutStudio,
  startSession,
  test,
} from './studio';

skipWithoutStudio();

const PROD = 'Shop (PROD)';

/** Every request that opens a connection on the server. */
function watchOpens(page: Page): string[] {
  const opens: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/connections')
      opens.push(request.postData() ?? '');
  });
  return opens;
}

async function connectFromConnections(page: Page): Promise<void> {
  await goTo(page, 'Verbindungen');
  await page.getByTestId('connection-item').filter({ hasText: PROD }).click();
  await page
    .getByTestId('connection-detail')
    .getByRole('button', { name: 'Verbinden', exact: true })
    .click();
}

test('S1-21 "Verbinden" on "Shop (PROD)" asks "Produktionsdatenbank" with the focus on "Abbrechen"; Cancel connects nothing', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  const opens = watchOpens(page);
  await connectFromConnections(page);
  const dialog = prodDialog(page);
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(
    'Shop (PROD) ist als PROD gekennzeichnet. Studio bleibt schreibgeschützt; Abfragen sind auf 15 s begrenzt.',
  );
  await expect(dialog.getByRole('button', { name: 'Abbrechen', exact: true })).toBeFocused();
  await dialog.getByRole('button', { name: 'Abbrechen', exact: true }).click();
  await expect(dialog).toBeHidden();
  await settled(page);
  expect(opens).toEqual([]);
  await expect(page.getByRole('banner')).not.toContainText(PROD);
});

test('S1-22 after confirming, the page is marked PROD and shows "Schreiben gesperrt" with its tooltip; a click opens nothing', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await connectFromConnections(page);
  await prodDialog(page).getByRole('button', { name: 'Verbinden', exact: true }).click();
  const header = page.getByRole('banner');
  await expect(header.getByTestId('connection-switcher')).toContainText(PROD);
  await expect(header.getByTestId('env-badge')).toHaveAttribute('data-env', 'prod');
  await expect(header.getByTestId('env-badge')).toContainText('PROD');
  await expect(page).toHaveTitle(/^\[PROD\] /);
  const chip = header.getByTestId('access-mode');
  await expect(chip).toHaveText(/^Schreiben gesperrt/);
  await expect(chip).toHaveAttribute(
    'title',
    'Produktionsverbindung: Schreiben ist immer gesperrt.',
  );
  await chip.hover();
  await chip.click();
  await settled(page);
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('S1-23 the writing example script on "Shop (PROD)" is refused before it runs; nothing changes', async ({
  page,
  studio,
}) => {
  const digest = () =>
    createHash('sha256').update(studio.read('daten/shop-prod.sqlite')).digest('hex');
  const before = digest();
  await startSession(page, studio);
  await openEditor(page, PROD);
  await page.getByRole('button', { name: 'Öffnen…', exact: true }).click();
  await page
    .getByRole('dialog', { name: 'Skript öffnen' })
    .getByRole('button', { name: 'skripte/03_schreibversuch_preiserhoehung.sql', exact: true })
    .click();
  await expect(page.locator('.cm-content')).toContainText('UPDATE products');
  await page.getByRole('button', { name: 'Ausführen', exact: true }).click();
  // D-02 may ask first (the first PROD connection of this page); confirming still runs nothing that writes.
  const ask = prodDialog(page);
  const refused = page.getByTestId('result-status');
  await expect(
    ask.or(refused.filter({ hasText: 'Studio führt nur lesende Statements aus' })),
  ).toBeVisible();
  if (await ask.isVisible())
    await ask.getByRole('button', { name: 'Verbinden', exact: true }).click();
  await expect(refused).toContainText('Studio führt nur lesende Statements aus: SELECT, WITH');
  expect(digest()).toBe(before);
  expect(studio.exists('daten/shop-prod.sqlite-journal')).toBe(false);
  expect(studio.exists('daten/shop-prod.sqlite-wal')).toBe(false);
});

test('S1-24 after a reload, opening "Shop (PROD)" in the explorer asks "Produktionsdatenbank" again', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await connectFromConnections(page);
  await prodDialog(page).getByRole('button', { name: 'Verbinden', exact: true }).click();
  await expect(page.getByRole('banner').getByTestId('connection-switcher')).toContainText(PROD);
  await page.reload();
  await expect(page.getByTestId('backend-status')).toBeVisible();
  await goTo(page, 'Explorer');
  await chooseProfile(page, 'Verbindungsprofil', PROD);
  await page.getByRole('button', { name: 'Öffnen', exact: true }).click();
  await expect(prodDialog(page)).toBeVisible();
  await expect(prodDialog(page)).toContainText('Shop (PROD) ist als PROD gekennzeichnet.');
});
