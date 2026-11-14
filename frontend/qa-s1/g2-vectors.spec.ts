/**
 * QA-S1 v2, section G, "Vektordaten" (S1-35 to S1-37): the read-only vector data view on the demo's product search
 * index in Orkexa's SQLite layout (revision 0002 of the v2 demo: `index_spaces` and `index_vectors`).
 */
import { createHash } from 'node:crypto';

import type { Page } from '@playwright/test';

import { chooseProfile, expect, goTo, skipWithoutStudio, startSession, test } from './studio';

skipWithoutStudio();

async function inspect(page: Page): Promise<void> {
  await goTo(page, 'Vektordaten');
  await chooseProfile(page, 'Verbindungsprofil', 'Shop (DEV)');
  await page.getByRole('button', { name: 'Prüfen', exact: true }).click();
  await expect(page.getByTestId('vectors-result')).toBeVisible();
}

test('S1-35 "Prüfen" on "Shop (DEV)" finds 1 vector column with exact search; sqlite-vec is "nicht feststellbar"', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await inspect(page);
  await expect(page.getByTestId('vectors-message')).toHaveText('Fertig: 1 Vektorspalte.');
  // Next to "Shop (DEV)": the summary of the columns.
  await expect(
    page
      .getByRole('paragraph')
      .filter({ hasText: 'Shop (DEV)' })
      .filter({ hasText: '1 Vektorspalte: 0 mit ANN-Index, 1 mit exakter Suche' }),
  ).toHaveCount(1);
  const result = page.getByTestId('vectors-result');
  await expect(result.getByRole('heading', { name: 'Erweiterung', exact: true })).toBeVisible();
  await expect(result).toContainText('sqlite-vec');
  await expect(result).toContainText('Status: nicht feststellbar.');
});

test('S1-36 "Vektorspalten" and "Embedding-Modelle" describe main.index_vectors.embedding and the model of "produkte"', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await inspect(page);
  const columns = page.getByRole('region', { name: 'Vektorspalten', exact: true });
  await expect(columns.getByRole('heading', { name: 'Vektorspalten', exact: true })).toBeVisible();
  // One row besides the header row.
  await expect(columns.getByRole('row')).toHaveCount(2);
  const column = columns.getByRole('row').nth(1);
  for (const text of [
    'main.index_vectors.embedding',
    'Orkexa-Layout',
    'BLOB',
    '8',
    'Exakte Suche (kein ANN-Index)',
    '120',
    'Kein Index',
  ]) {
    await expect(column).toContainText(text);
  }
  const result = page.getByTestId('vectors-result');
  await expect(
    result.getByRole('heading', { name: 'Embedding-Modelle', exact: true }),
  ).toBeVisible();
  const models = result
    .getByRole('table')
    .filter({ has: page.getByRole('columnheader', { name: 'Vektorraum', exact: true }) });
  await expect(models.getByRole('row')).toHaveCount(2);
  const identity = models.getByRole('row').nth(1);
  for (const text of [
    'produkte',
    'Namensraum shop',
    'shop-embed-mini von beispiel',
    'Build 2025-10-01 des Modells',
    'Normalisierte Vektoren',
    '8',
    'active',
    '120',
  ]) {
    await expect(identity).toContainText(text);
  }
});

test('S1-37 the hint says sqlite-vec cannot be seen in the file; "Erneut prüfen" gives the same result; nothing writes', async ({
  page,
  studio,
}) => {
  const digest = () => createHash('sha256').update(studio.read('daten/shop.sqlite')).digest('hex');
  const unchanged = digest();
  await startSession(page, studio);
  await inspect(page);
  const hints = page.getByRole('region', { name: 'Hinweise', exact: true });
  await expect(hints.getByRole('heading', { name: 'Hinweise', exact: true })).toBeVisible();
  await expect(hints).toContainText('Hinweis:');
  await expect(hints).toContainText(
    'Ob sqlite-vec installiert ist, lässt sich an der Datenbankdatei nicht erkennen',
  );
  const result = page.getByTestId('vectors-result');
  const before = await result.innerText();
  await page.getByRole('button', { name: 'Erneut prüfen', exact: true }).click();
  await expect(page.getByTestId('vectors-message')).toHaveText('Fertig: 1 Vektorspalte.');
  await expect.poll(() => result.innerText()).toBe(before);
  expect(digest()).toBe(unchanged);
  expect(studio.exists('daten/shop.sqlite-journal')).toBe(false);
  expect(studio.exists('daten/shop.sqlite-wal')).toBe(false);
  await expect(page.getByRole('main')).toContainText('Nur lesend:');
  await expect(
    page
      .getByRole('main')
      .getByRole('button', { name: /index|indizier|einbett|ändern|bauen|löschen|schreib/i }),
  ).toHaveCount(0);
});
