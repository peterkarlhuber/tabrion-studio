/**
 * STU-015: the migration status view in Chromium. The view, the code trust dialog and both status results (code not
 * trusted, then trusted) have no serious or critical axe violation in light and dark, English and German; trusting
 * sends the digest the dialog showed and every write is JSON, DELETE included.
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

const TEXT = {
  en: {
    review: 'Review files',
    trust: 'Trust and run',
    show: 'Show status',
    revoke: 'Stop trusting',
  },
  de: {
    review: 'Dateien prüfen',
    trust: 'Vertrauen und ausführen',
    show: 'Stand anzeigen',
    revoke: 'Nicht mehr vertrauen',
  },
};

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as const) {
    test(`${theme}, ${lang}: view, trust dialog and status pass axe`, async ({ page }) => {
      const text = TEXT[lang];
      await openHarness(page, { scenario: 'migrations', path: '/migrations', theme, lang });
      await expect(page.getByRole('button', { name: text.review })).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
      await page.getByRole('button', { name: text.show }).click();
      await expect(page.getByTestId('migrations-alembic')).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
      await page.getByRole('button', { name: text.review }).click();
      const dialog = page.getByRole('dialog');
      await expect(dialog.getByTestId('trust-code-files')).toContainText('0003_indexes.py');
      expect(await seriousViolations(page)).toEqual([]);
      await dialog.getByRole('button', { name: text.trust }).click();
      await expect(page.getByRole('dialog')).toHaveCount(0);
      await expect(page.getByTestId('migrations-unknown')).toContainText('ghost-1');
      expect(await seriousViolations(page)).toEqual([]);
    });
  }
}

test('trust and revoke: the digest the dialog showed, every write JSON', async ({ page }) => {
  await openHarness(page, { scenario: 'migrations', path: '/migrations' });
  await page.getByRole('button', { name: 'Review files' }).click();
  const dialog = page.getByRole('dialog', { name: "Run the project's migration code?" });
  await expect(dialog.getByRole('button', { name: "Don't trust" })).toBeFocused();
  await dialog.getByRole('button', { name: 'Trust and run' }).click();
  await expect(page.getByTestId('migrations-message')).toHaveText('The migration code is trusted.');
  await page.getByRole('button', { name: 'Stop trusting' }).click();
  await expect(page.getByTestId('migrations-message')).toHaveText(
    'The migration code is no longer trusted.',
  );
  const sent = await writes(page);
  expect(sent.map((w) => `${w.method} ${w.url}`)).toEqual([
    'POST /api/project/code-trust',
    'DELETE /api/project/code-trust',
  ]);
  for (const write of sent) expect(write.contentType).toBe('application/json');
});
