/**
 * STU-XDE-1 in the browser: the Data Editor's type editors, the checks at the cell and the change overview, against
 * the harness's fake server (`src/harness/dataEditor.ts`, scenario `data-editor`: an unlocked table with a boolean,
 * an enum, a numeric(6,2), a date, a timestamptz, a UUID, JSON, an int4 array, bytes and a varchar(8)). The real
 * server's checks and the database are covered by tests/security/test_data_editor*.py; this checks what the page
 * does with the types: the right editor per cell, a value that does not fit marked at its cell and holding the
 * preview, the changes before and after, NULL told apart from the empty text, and the server's refusal placed at its
 * cell. axe finds no serious or critical violation in light and dark, English and German. Gate G-S2 is not granted;
 * the harness is never built into the wheel.
 */
import { test, expect, type Page } from '@playwright/test';

import { seriousViolations, type Theme } from './support';

interface DataEditorLog {
  plans: { request: { changes: unknown[] } }[];
  executed: number;
}

const log = (page: Page) =>
  page.evaluate(() => (window as unknown as { dataEditorLog: DataEditorLog }).dataEditorLog);

async function open(page: Page, theme: Theme = 'light', lang: 'en' | 'de' = 'en') {
  await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
  await page.goto(`/harness.html?scenario=data-editor&theme=${theme}&lang=${lang}`);
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await expect(page.getByTestId('data-editor-grid')).toBeVisible();
}

const cell = (page: Page, name: string) => page.getByLabel(name, { exact: true });

test('each column gets its editor, and the changes reach the plan as the column takes them', async ({
  page,
}) => {
  await open(page);
  // Choices for boolean and enum, with NULL where the column takes it.
  const ok = cell(page, 'Row 2, ok');
  await expect(ok.locator('option')).toHaveText(['NULL', 'true', 'false']);
  await ok.selectOption('false');
  await cell(page, 'Row 2, mood').selectOption('unknown');
  // Text fields for the rest; the header names the format.
  await expect(page.getByRole('columnheader', { name: /made/ })).toContainText('date');
  await cell(page, 'Row 2, price').fill('12.5');
  await cell(page, 'Row 2, made').fill('2024-02-29');
  await cell(page, 'Row 2, seen').fill('2025-01-01 10:00+02:00');
  await cell(page, 'Row 2, tag').fill('A0EEBC99-9C0B-4EF8-BB6D-6BB9BD380A11');
  await cell(page, 'Row 2, doc').fill('{"b": [1, 2]}');
  await cell(page, 'Row 2, nums').fill('[[1, 2], [3, null]]');
  // Bytes are read only.
  await expect(cell(page, 'Row 1, raw')).toHaveAttribute('readonly', '');

  const overview = page.getByTestId('data-editor-changes');
  await expect(overview).toContainText('Rows: 1 changed, 0 new, 0 deleted');
  await expect(overview.getByRole('row')).toHaveText([
    'ColumnBeforeAfter',
    'okNULLfalse',
    'moodNULLunknown',
    'priceNULL12.5',
    'madeNULL2024-02-29',
    'seenNULL2025-01-01 10:00+02:00',
    'tagNULLA0EEBC99-9C0B-4EF8-BB6D-6BB9BD380A11',
    'docNULL{"b":[1,2]}',
    'numsNULL[[1,2],[3,null]]',
  ]);
  await page.getByRole('button', { name: 'Preview' }).click();
  await expect(page.getByTestId('data-editor-review-changes')).toContainText('okNULLfalse');
  const sent = (await log(page)).plans[0]?.request.changes;
  expect(sent).toEqual([
    {
      op: 'update',
      key: { id: 2 },
      old: {
        id: 2,
        name: 'nut',
        ok: null,
        mood: null,
        price: null,
        made: null,
        seen: null,
        tag: null,
        doc: null,
        nums: null,
        raw: null,
      },
      new: {
        ok: false,
        mood: 'unknown',
        price: '12.5',
        made: '2024-02-29',
        seen: '2025-01-01 10:00+02:00',
        tag: 'A0EEBC99-9C0B-4EF8-BB6D-6BB9BD380A11',
        doc: { b: [1, 2] },
        nums: [
          [1, 2],
          [3, null],
        ],
      },
    },
  ]);
  await page.getByLabel('Type parts-w to confirm').fill('parts-w');
  await page.getByRole('button', { name: 'Confirm' }).click();
  await page.getByTestId('data-editor-execute').click();
  await expect(page.getByTestId('data-editor-saved')).toBeVisible();
  expect((await log(page)).executed).toBe(1);
});

test('a value that does not fit is marked at its cell and holds the preview', async ({ page }) => {
  await open(page);
  const preview = page.getByRole('button', { name: 'Preview' });
  const cases: [string, string, RegExp, string][] = [
    ['Row 1, price', '12345.678', /Out of the range of this column/, '1234.5'],
    ['Row 1, made', '2023-02-29', /expected a date as YYYY-MM-DD/, '2023-02-28'],
    ['Row 1, seen', '2025-01-01 10:00', /with its offset/, '2025-01-01 10:00Z'],
    ['Row 1, tag', 'not-a-uuid', /expected a UUID/, 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a12'],
    ['Row 1, doc', '{"a": ', /expected a JSON value/, '{"a": 2}'],
    ['Row 1, nums', '[[1], [2, 3]]', /expected a JSON array/, '[1, 2, 3]'],
    ['Row 1, name', 'ninechars', /Too long: at most 8 characters/, 'eight ch'],
  ];
  for (const [label, wrong, issue, right] of cases) {
    const field = cell(page, label);
    await field.fill(wrong);
    await expect(field).toHaveAttribute('aria-invalid', 'true');
    await expect(field).toHaveAccessibleDescription(issue);
    await expect(preview).toBeDisabled();
    await expect(page.getByTestId('data-editor-issues')).toContainText(
      label.replace(', ', ', column '),
    );
    await field.fill(right);
    await expect(field).not.toHaveAttribute('aria-invalid', 'true');
  }
  await expect(page.getByTestId('data-editor-issues')).toHaveCount(0);
  await expect(preview).toBeEnabled();
  expect((await log(page)).plans).toEqual([]);
});

test('NULL is not the empty text; new rows start at their defaults and name what they need', async ({
  page,
}) => {
  await open(page);
  // name is NOT NULL: no NULL button; emptied, it is the empty text.
  await expect(page.getByRole('button', { name: 'Set Row 1, name to NULL' })).toHaveCount(0);
  await cell(page, 'Row 1, name').fill('');
  await page.getByRole('button', { name: 'Set Row 1, price to NULL' }).click();
  await expect(cell(page, 'Row 1, price')).toHaveAttribute('placeholder', 'NULL');
  const overview = page.getByTestId('data-editor-changes');
  await expect(overview.getByRole('row')).toHaveText([
    'ColumnBeforeAfter',
    'namebolt(empty text)',
    'price1.50NULL',
  ]);
  await page.getByRole('button', { name: 'Add row' }).click();
  await expect(cell(page, 'New row 1, ok')).toHaveValue('\u0000default');
  await expect(cell(page, 'New row 1, name')).toHaveAccessibleDescription(
    'Required: this column has no default and takes no NULL.',
  );
  await expect(page.getByRole('button', { name: 'Preview' })).toBeDisabled();
  await cell(page, 'New row 1, name').fill('pin');
  await cell(page, 'New row 1, mood').selectOption('bad');
  await expect(overview).toContainText('New row 1');
  await expect(overview).toContainText('Columns not set get their default.');
  await page.getByRole('button', { name: 'Delete row' }).nth(2).click();
  await expect(overview).toContainText('Delete the row id = 3');
  await expect(overview).toContainText('Rows: 1 changed, 1 new, 1 deleted');
  await page.getByRole('button', { name: 'Preview' }).click();
  await expect(page.getByLabel('Type parts to confirm')).toBeVisible();
  expect((await log(page)).plans[0]?.request.changes).toEqual([
    {
      op: 'update',
      key: { id: 1 },
      old: expect.objectContaining({ id: 1, name: 'bolt', price: '1.50' }) as unknown,
      new: { name: '', price: null },
    },
    { op: 'insert', values: { name: 'pin', mood: 'bad' } },
    { op: 'delete', key: { id: 3 }, old: expect.objectContaining({ id: 3 }) as unknown },
  ]);
});

test("the server's refusal is placed at its cell", async ({ page }) => {
  await open(page);
  const name = cell(page, 'Row 2, name');
  await name.fill('refuse');
  await page.getByRole('button', { name: 'Preview' }).click();
  await expect(page.getByTestId('data-editor-error')).toContainText(
    'the cells in question are marked',
  );
  await expect(name).toHaveAttribute('aria-invalid', 'true');
  await expect(name).toHaveAccessibleDescription('Not one of the allowed values.');
  await name.fill('fine');
  await expect(name).not.toHaveAttribute('aria-invalid', 'true');
});

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as const) {
    test(`${theme}, ${lang}: the editors, an issue and the overview pass axe`, async ({ page }) => {
      await open(page, theme, lang);
      await cell(page, lang === 'en' ? 'Row 2, ok' : 'Zeile 2, ok').selectOption('true');
      const price = cell(page, lang === 'en' ? 'Row 1, price' : 'Zeile 1, price');
      await price.fill('99999');
      await expect(price).toHaveAttribute('aria-invalid', 'true');
      await page
        .getByRole('button', { name: lang === 'en' ? 'Add row' : 'Zeile hinzufügen' })
        .click();
      await expect(page.getByTestId('data-editor-changes')).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
    });
  }
}
