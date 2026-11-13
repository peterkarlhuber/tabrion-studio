/**
 * STU-022 in the explorer page, in Chromium: the DDL tab, the policies tab and SQLite's row count on request have no
 * serious or critical axe violation in light and dark, English and German; the DDL region takes the focus and keeps
 * hostile text escaped; the page sends no write but the connection open.
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
  en: { policies: 'Policies', count: 'Count rows', sequences: 'Sequences (1)' },
  de: { policies: 'Policies', count: 'Zeilen zählen', sequences: 'Sequenzen (1)' },
};

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as const) {
    test(`${theme}, ${lang}: DDL, policies and the row count pass axe`, async ({ page }) => {
      // Seven full axe runs (each 2-3 s with color contrast): more than the default 30 s on a busy runner.
      test.setTimeout(90_000);
      const text = TEXT[lang];
      await openHarness(page, { scenario: 'explorer', path: '/explorer', theme, lang });
      await page.getByTestId('explorer-open').click();
      // SQLite: the row count on request, then one past the server's timeout.
      await page.getByRole('treeitem', { name: 'part', exact: true }).click();
      await expect(page.getByTestId('detail-column')).toHaveCount(8);
      await page.getByRole('button', { name: text.count }).click();
      await expect(page.getByTestId('row-count-result')).toContainText('1');
      await page.getByRole('tab', { name: 'DDL' }).click();
      await expect(page.getByTestId('ddl-text')).toContainText('CREATE TABLE part');
      expect(await seriousViolations(page)).toEqual([]);
      await page.getByRole('treeitem', { name: 'material', exact: true }).click();
      await page.getByRole('button', { name: text.count }).click();
      await expect(page.getByTestId('row-count-result')).toHaveClass(/problem/);
      expect(await seriousViolations(page)).toEqual([]);
      // PostgreSQL (TEST profile): the DDL, the policies of a table, RLS without policies, a sequence's DDL.
      await page.getByRole('combobox').first().selectOption('project:parts-pg');
      await page.getByTestId('explorer-open').click();
      await page.getByRole('treeitem', { name: 'measurement', exact: true }).click();
      await page.getByRole('tab', { name: 'DDL' }).click();
      await expect(page.getByTestId('ddl-text')).toContainText('ENABLE ROW LEVEL SECURITY');
      expect(await seriousViolations(page)).toEqual([]);
      await page.getByRole('tab', { name: text.policies }).click();
      await expect(page.getByTestId('detail-policy')).toHaveCount(2);
      expect(await seriousViolations(page)).toEqual([]);
      await page.getByRole('treeitem', { name: 'device', exact: true }).click();
      await expect(page.getByTestId('policies-deny')).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
      await page.getByRole('treeitem', { name: text.sequences }).click();
      await page.getByRole('treeitem', { name: 'measurement_id_seq' }).click();
      await expect(page.getByTestId('ddl-text')).toContainText('CREATE SEQUENCE');
      expect(await seriousViolations(page)).toEqual([]);
    });
  }
}

test('the DDL region takes the focus, keeps hostile text escaped, and Copy DDL copies the exact text', async ({
  page,
  context,
  browserName,
}) => {
  // Only Chromium knows the clipboard permissions, so a page can read the clipboard back there alone (TD-047). In
  // every browser the text the page writes is recorded on its way to the real clipboard, whose answer still decides
  // what the page says.
  if (browserName === 'chromium')
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.addInitScript(() => {
    const written: string[] = [];
    (window as unknown as { written: string[] }).written = written;
    const clipboard = navigator.clipboard;
    const writeText = clipboard.writeText.bind(clipboard);
    clipboard.writeText = (text: string) => {
      written.push(text);
      return writeText(text);
    };
  });
  await openHarness(page, { scenario: 'explorer', path: '/explorer' });
  await page.getByTestId('explorer-open').click();
  await page.getByRole('treeitem', { name: /^notes/ }).click();
  await page.getByRole('tab', { name: 'DDL' }).click();
  const ddl = page.getByTestId('ddl-text');
  await expect(ddl).toContainText('\\u202E');
  expect(await ddl.textContent()).not.toContain('‮');
  await expect(ddl.locator('b')).toHaveCount(0);
  await ddl.focus();
  await expect(ddl).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Copy DDL' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('ddl-copy-status')).toHaveText('The DDL is on the clipboard.');
  // Exactly the DDL the server sent (src/harness/explorer.ts), control characters and markup included, once.
  expect(await page.evaluate(() => (window as unknown as { written: string[] }).written)).toEqual([
    'CREATE TABLE "notes\u202etxt.exe\nx" (body\u202eevil TEXT) -- \\ \r<b>bold</b>;\n\n',
  ]);
  if (browserName === 'chromium') {
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    expect(copied).toBe(
      'CREATE TABLE "notes\u202etxt.exe\nx" (body\u202eevil TEXT) -- \\ \r<b>bold</b>;\n\n',
    );
  }
  const sent = await writes(page);
  expect(sent.map((w) => `${w.method} ${w.url}`)).toEqual(['POST /api/connections']);
});
