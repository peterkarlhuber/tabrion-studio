/**
 * QA-S1 section F, "Migrationsstand": the version in the database while the project's Alembic files are not trusted,
 * the trust question for the migration code, the pending revision 0003, and the copied upgrade command.
 */
import type { Page } from '@playwright/test';

import { chooseProfile, expect, goTo, skipWithoutStudio, startSession, test } from './studio';

skipWithoutStudio();

/** "Migrationen" → "Shop (DEV)" → "Stand anzeigen". */
async function showStatus(page: Page): Promise<void> {
  await goTo(page, 'Migrationen');
  await chooseProfile(page, 'Verbindungsprofil', 'Shop (DEV)');
  await page.getByRole('button', { name: 'Stand anzeigen', exact: true }).click();
  await expect(page.getByTestId('migrations-result')).toBeVisible();
}

/** "Dateien prüfen" → "Vertrauen und ausführen". */
async function trustCode(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Dateien prüfen', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Migrationscode des Projekts ausführen?' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Vertrauen und ausführen', exact: true }).click();
  await expect(dialog).toBeHidden();
}

test('S1-25 "Stand anzeigen" shows "Version in der Datenbank"; the revisions are "unbekannt" until the code is trusted', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await showStatus(page);
  const alembic = page.getByTestId('migrations-alembic');
  await expect(alembic).toContainText('Version in der Datenbank');
  await expect(alembic).toContainText('0002');
  await expect(alembic).toContainText('unbekannt');
  await expect(page.getByRole('main')).toContainText(
    'Die Revisionen sind unbekannt, weil dem Migrationscode des Projekts nicht vertraut wird.',
  );
  await expect(page.getByTestId('code-trust-state')).toHaveText(
    'Den Alembic-Dateien des Projekts wird nicht vertraut. Studio zeigt die Version in der Datenbank, die Revisionen aber erst, wenn du ihnen vertraust.',
  );
});

test('S1-26 "Dateien prüfen" lists the Alembic files with the focus on "Nicht vertrauen"; after trusting, 1 revision is pending', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await showStatus(page);
  const section = page.getByRole('region', { name: 'Migrationscode' });
  await expect(section).toBeVisible();
  await section.getByRole('button', { name: 'Dateien prüfen', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Migrationscode des Projekts ausführen?' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Nicht vertrauen', exact: true })).toBeFocused();
  await expect(dialog).toContainText('Alembic-Konfiguration');
  await expect(dialog).toContainText('alembic.ini');
  await expect(dialog).toContainText('Revisionsdateien');
  for (const file of [
    '0001_initial_shop_schema.py',
    '0002_payments_shipments_reviews.py',
    '0003_discontinued_products.py',
  ]) {
    await expect(dialog).toContainText(file);
  }
  await dialog.getByRole('button', { name: 'Vertrauen und ausführen', exact: true }).click();
  await expect(dialog).toBeHidden();
  const alembic = page.getByTestId('migrations-alembic');
  await expect(alembic).toContainText('1 Revision ist noch nicht angewendet.');
  await expect(alembic).toContainText('Noch nicht angewendet');
  await expect(alembic).toContainText('0003');
});

test.describe(() => {
  test.use({ permissions: ['clipboard-read', 'clipboard-write'] });

  test('S1-27 "Befehl kopieren" puts "alembic upgrade head" on the clipboard; Studio offers no button that migrates', async ({
    page,
    studio,
  }) => {
    await startSession(page, studio);
    await showStatus(page);
    await trustCode(page);
    await expect(page.getByTestId('migrations-alembic')).toContainText(
      '1 Revision ist noch nicht angewendet.',
    );
    await page.getByRole('button', { name: 'Befehl kopieren', exact: true }).click();
    await expect(page.getByTestId('migrations-command-status')).toHaveText(
      'Der Befehl ist in der Zwischenablage.',
    );
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('alembic upgrade head');
    await expect(
      page.getByRole('button', { name: /migrier|upgrade|anwenden|ausführen|aktualisier/i }),
    ).toHaveCount(0);
  });
});
