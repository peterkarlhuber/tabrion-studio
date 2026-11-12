/**
 * STU-010: the explorer page in Chromium. The page, the open tree, the detail tabs, the SQLite file facts and the
 * PostgreSQL extensions have no serious or critical axe violation in light and dark, English and German; the tree
 * and the tabs work with the keyboard alone; the page sends no write but the connection open.
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
  en: { tables: 'Tables (6)', relationships: 'Relationships' },
  de: { tables: 'Tabellen (6)', relationships: 'Beziehungen' },
};

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as const) {
    test(`${theme}, ${lang}: tree, detail tabs, file facts and extensions pass axe`, async ({
      page,
    }) => {
      // Nine full axe runs (each 2-3 s with colour contrast): more than the default 30 s on a busy runner.
      test.setTimeout(90_000);
      const text = TEXT[lang];
      await openHarness(page, { scenario: 'explorer', path: '/explorer', theme, lang });
      await expect(page.getByTestId('explorer-open')).toBeEnabled();
      expect(await seriousViolations(page)).toEqual([]);
      await page.getByTestId('explorer-open').click();
      await expect(page.getByRole('treeitem', { name: text.tables })).toBeVisible();
      await page.getByRole('treeitem', { name: 'part', exact: true }).click();
      await expect(page.getByTestId('detail-column')).toHaveCount(8);
      expect(await seriousViolations(page)).toEqual([]);
      for (const tab of await page.getByRole('tab').all()) {
        await tab.click();
        expect(await seriousViolations(page)).toEqual([]);
      }
      await page.getByRole('treeitem').first().click();
      await expect(page.getByTestId('detail-file')).toBeVisible();
      await page.getByTestId('explorer-filter').fill('par');
      await expect(page.getByTestId('explorer-matches')).not.toBeEmpty();
      expect(await seriousViolations(page)).toEqual([]);
      // PostgreSQL (TEST profile): schemas, extensions with an update.
      await page.getByRole('combobox').first().selectOption('project:parts-pg');
      await page.getByTestId('explorer-open').click();
      await page.getByRole('treeitem').last().click();
      await expect(page.getByTestId('detail-extension')).toHaveCount(2);
      expect(await seriousViolations(page)).toEqual([]);
      await page.getByRole('treeitem', { name: 'measurement', exact: true }).click();
      await page.getByRole('tab', { name: text.relationships }).click();
      await expect(page.getByTestId('detail-references')).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
    });
  }
}

test('keyboard only: open, move through the tree, select, switch tabs, follow a key', async ({
  page,
}) => {
  await openHarness(page, { scenario: 'explorer', path: '/explorer' });
  await page.getByTestId('explorer-open').focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('tree')).toBeVisible();
  // The focus stays on "Open" while it works and after (C-2 of stu-014-ui-review.md).
  await expect(page.getByTestId('explorer-open')).toBeFocused();
  await page.keyboard.press('Control+p');
  await expect(page.getByTestId('explorer-filter')).toBeFocused();
  // Tab from the filter: the type picker, then the tree's single tab stop.
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('treeitem', { name: 'Database file' })).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('treeitem', { name: 'alembic_version' })).toBeFocused();
  await page.keyboard.press('p');
  await expect(page.getByRole('treeitem', { name: 'part', exact: true })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('detail-column')).toHaveCount(8);
  await expect(page.getByRole('treeitem', { name: 'part', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  // Tab leaves the tree for SQLite's "Count rows" (STU-022), then the detail tabs; ← → move between them
  // (← from the first wraps to the last, DDL).
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Count rows' })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('tab', { name: 'Columns' })).toBeFocused();
  await page.keyboard.press('ArrowLeft');
  await expect(page.getByRole('tab', { name: 'DDL' })).toBeFocused();
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('ArrowLeft');
  await expect(page.getByRole('tab', { name: 'Relationships' })).toBeFocused();
  // Into the panel and to the first related table; Enter follows it and focuses its tree row.
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Go to main.material' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('treeitem', { name: 'material' })).toBeFocused();
  await expect(page.getByRole('treeitem', { name: 'material' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  expect(await page.evaluate(() => document.activeElement === document.body)).toBe(false);
});

test('PROD asks D-02 first; the only write is the connection open, as JSON', async ({ page }) => {
  await openHarness(page, { scenario: 'explorer', path: '/explorer' });
  await page.getByRole('combobox').first().selectOption('user:measure-central');
  await page.getByTestId('explorer-open').click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Connect' }).click();
  await expect(page.getByRole('treeitem', { name: 'public (6)' })).toBeVisible();
  await expect(page.getByTestId('explorer-workspace').getByTestId('env-badge')).toHaveAttribute(
    'data-env',
    'prod',
  );
  await page.getByTestId('explorer-refresh').click();
  await expect(page.getByTestId('explorer-message')).toContainText('6 objects');
  const sent = await writes(page);
  expect(sent.map((w) => `${w.method} ${w.url}`)).toEqual(['POST /api/connections']);
  expect(sent[0]?.contentType).toBe('application/json');
});
