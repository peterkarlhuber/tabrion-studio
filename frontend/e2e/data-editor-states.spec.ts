/**
 * STU-XDE-2 in the browser: the states of the Data Editor that the other specs do not reach, against the harness's
 * fake server (`src/harness/dataEditor.ts`, scenario `data-editor` with its options): the unlock (D-03) and the PROD
 * lock, the refused tables (a column without equality, a partitioned table, no primary key, not a table), the notice
 * for triggers, rules and row security (a notice, not a block: owner decision 2026-11-15), the review before and
 * after the confirmation, and the conflict that rolls everything back. axe finds no serious or critical violation in
 * any of these states, in light and dark, English and German; no catalog key shows as raw text. The real server's
 * refusals are covered by tests/security/test_data_editor*.py and e2e/data-editor-backend.spec.ts. Gate G-S2 is not
 * granted; the harness is never built into the wheel.
 */
import { readFileSync } from 'node:fs';

import { test, expect, type Page } from '@playwright/test';

import { seriousViolations, type Theme } from './support';

type Language = 'en' | 'de';
type Tree = { [key: string]: string | Tree };
const catalog = (lang: Language) =>
  JSON.parse(
    readFileSync(new URL(`../src/i18n/locales/${lang}.json`, import.meta.url), 'utf8'),
  ) as Tree;
const CATALOGS: Record<Language, Tree> = { en: catalog('en'), de: catalog('de') };

/** A catalog text with its `{{name}}` placeholders filled in. */
function text(lang: Language, key: string, values: Record<string, string> = {}): string {
  let node: string | Tree = CATALOGS[lang];
  for (const part of key.split('.')) {
    if (typeof node === 'string' || !(part in node)) throw new Error(`no catalog text ${key}`);
    node = node[part] as string | Tree;
  }
  if (typeof node !== 'string') throw new Error(`no catalog text ${key}`);
  return node.replace(/\{\{(\w+)\}\}/g, (_, name: string) => values[name] ?? '');
}

interface DataEditorLog {
  plans: unknown[];
  executed: number;
}

const log = (page: Page) =>
  page.evaluate(() => (window as unknown as { dataEditorLog: DataEditorLog }).dataEditorLog);

async function open(
  page: Page,
  options: Record<string, string> = {},
  theme: Theme = 'light',
  lang: Language = 'en',
) {
  const params = new URLSearchParams({ scenario: 'data-editor', theme, lang, ...options });
  await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
  await page.goto(`/harness.html?${params.toString()}`);
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await expect(page.locator('html')).toHaveAttribute('lang', lang);
}

/** No catalog key (`dataEditor.…`, `problems.…`) is shown as text instead of its translation. */
async function expectNoRawKeys(page: Page) {
  await expect(page.locator('main')).not.toContainText(/\b(dataEditor|problems)\.[a-z]/);
}

async function unlock(page: Page, lang: Language = 'en') {
  const form = page.getByTestId('data-editor-unlock');
  await expect(form).toBeVisible();
  await form.getByLabel(text(lang, 'dataEditor.unlock.type', { name: 'parts-w' })).fill('parts-w');
  await form.getByRole('button', { name: text(lang, 'dataEditor.unlock.submit') }).click();
}

test('the unlock asks for the typed write profile name (D-03); Lock ends it', async ({ page }) => {
  await open(page, { locked: '1' });
  const form = page.getByTestId('data-editor-unlock');
  await expect(form.getByRole('heading', { name: 'Unlock write mode' })).toBeVisible();
  const field = form.getByLabel('Type parts-w to unlock');
  const submit = form.getByRole('button', { name: 'Unlock' });
  await expect(submit).toBeDisabled();
  // Exactly the name: another case or a trailing space does not unlock.
  for (const wrong of ['PARTS-W', 'parts-w ', 'parts']) {
    await field.fill(wrong);
    await expect(submit).toBeDisabled();
  }
  await expect(page.getByTestId('data-editor-grid')).toHaveCount(0);
  await unlock(page);
  await expect(page.getByTestId('data-editor-grid')).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: 'unlocked' })).toHaveText(
    'Write mode is unlocked for this connection.',
  );
  await page.getByRole('button', { name: 'Lock', exact: true }).click();
  await expect(page.getByTestId('data-editor-unlock')).toBeVisible();
  await expect(page.getByTestId('data-editor-grid')).toHaveCount(0);
});

test('PROD: the server refuses the unlock and no grid opens (E-X7)', async ({ page }) => {
  await open(page, { locked: 'prod' });
  await unlock(page);
  await expect(page.getByTestId('data-editor-unlock').getByRole('alert')).toHaveText(
    'Studio never writes to a PROD database.',
  );
  await expect(page.getByTestId('data-editor-grid')).toHaveCount(0);
  expect((await log(page)).plans).toEqual([]);
});

const REFUSALS: [string, RegExp][] = [
  ['data_editor_column_incomparable', /type that cannot be compared \(for example xml or point\)/],
  ['plan_target_partitioned', /does not change partitioned tables/],
  ['plan_target_no_primary_key', /has no primary key/],
  ['plan_target_invalid', /not a table of this database/],
];

for (const [code, message] of REFUSALS) {
  test(`a refused table (${code}) is refused before any edit`, async ({ page }) => {
    await open(page, { refuse: code });
    const refusal = page.getByTestId('data-editor-problem');
    await expect(refusal).toHaveText(message);
    await expect(refusal).toHaveAttribute('role', 'alert');
    await expect(page.getByTestId('data-editor-grid')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Preview' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Add row' })).toHaveCount(0);
  });
}

test('triggers, rules and row security: a notice, not a block; the review names them and the run goes ahead', async ({
  page,
}) => {
  await open(page, { triggers: '1' });
  await expect(page.getByTestId('data-editor-side-effects')).toHaveText(
    'This table has triggers or rules: parts_audit, parts_log. They run with every change and can change more ' +
      'rows; Studio does not count those. You can still edit the table; the review names them again.',
  );
  await expect(page.getByTestId('data-editor-row-security')).toContainText('Row security is on');
  await page.getByLabel('Row 2, name', { exact: true }).fill('nut M5');
  await page.getByRole('button', { name: 'Preview' }).click();
  const review = page.getByTestId('data-editor-review');
  await expect(review.getByRole('note')).toHaveText(
    'Triggers and rules also run: parts_audit, parts_log.',
  );
  await review.getByLabel('Type parts-w to confirm').fill('parts-w');
  await review.getByRole('button', { name: 'Confirm' }).click();
  await page.getByTestId('data-editor-execute').click();
  await expect(page.getByTestId('data-editor-saved')).toContainText('Saved: 1 updated');
  expect((await log(page)).executed).toBe(1);
});

test('the review: Back discards the plan, the confirmation needs the exact text, then one run', async ({
  page,
}) => {
  await open(page);
  await page.getByLabel('Row 2, name', { exact: true }).fill('nut M5');
  await page.getByRole('button', { name: 'Preview' }).click();
  const review = page.getByTestId('data-editor-review');
  await expect(review.getByRole('heading', { name: 'Review the changes' })).toBeVisible();
  await expect(review).toContainText('All changes run in one transaction.');
  // While the review is open, the grid takes no edit.
  await expect(page.getByLabel('Row 2, name', { exact: true })).not.toBeEditable();
  await review.getByRole('button', { name: 'Back' }).click();
  await expect(review).toHaveCount(0);
  await expect(page.getByLabel('Row 2, name', { exact: true })).toHaveValue('nut M5');

  await page.getByRole('button', { name: 'Preview' }).click();
  const confirm = review.getByRole('button', { name: 'Confirm' });
  await expect(confirm).toBeDisabled();
  await review.getByLabel('Type parts-w to confirm').fill('parts');
  await expect(confirm).toBeDisabled();
  await review.getByLabel('Type parts-w to confirm').fill('parts-w');
  await confirm.click();
  await expect(page.getByTestId('data-editor-execute')).toBeVisible();
  expect((await log(page)).executed).toBe(0);
  await page.getByTestId('data-editor-execute').click();
  await expect(page.getByTestId('data-editor-saved')).toBeVisible();
  expect((await log(page)).executed).toBe(1);
  expect((await log(page)).plans).toHaveLength(2);
});

test('a conflict at the run: nothing is saved, the message names no value, Reload starts again', async ({
  page,
}) => {
  await open(page, { conflict: '1' });
  await page.getByLabel('Row 2, name', { exact: true }).fill('nut M5');
  await page.getByRole('button', { name: 'Preview' }).click();
  await page.getByLabel('Type parts-w to confirm').fill('parts-w');
  await page.getByRole('button', { name: 'Confirm' }).click();
  await page.getByTestId('data-editor-execute').click();
  const problem = page.getByTestId('data-editor-error');
  await expect(problem).toHaveAttribute('role', 'alert');
  await expect(problem).toContainText(
    'A row was changed or deleted by someone else since it was loaded. Nothing was saved.',
  );
  await expect(problem).not.toContainText('nut');
  await expect(page.getByTestId('data-editor-saved')).toHaveCount(0);
  await problem.getByRole('button', { name: 'Reload the rows' }).click();
  await expect(page.getByLabel('Row 2, name', { exact: true })).toHaveValue('nut');
  await expect(page.getByTestId('data-editor-pending')).toHaveText('0 changes');
});

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as Language[]) {
    test(`${theme}, ${lang}: unlock, notices, review, confirmation and conflict pass axe`, async ({
      page,
    }) => {
      const t = (key: string, values: Record<string, string> = {}) => text(lang, key, values);
      await open(page, { locked: '1', triggers: '1', conflict: '1' }, theme, lang);
      await page
        .getByTestId('data-editor-unlock')
        .getByLabel(t('dataEditor.unlock.type', { name: 'parts-w' }))
        .fill('parts-w');
      expect(await seriousViolations(page)).toEqual([]);
      await expectNoRawKeys(page);

      await page.getByRole('button', { name: t('dataEditor.unlock.submit') }).click();
      await expect(page.getByTestId('data-editor-side-effects')).toBeVisible();
      await expect(page.getByTestId('data-editor-row-security')).toBeVisible();
      await expect(page.getByTestId('data-editor-help')).toHaveText(t('dataEditor.help'));
      await page
        .getByLabel(t('dataEditor.cell', { row: '2', column: 'name' }), { exact: true })
        .fill('nut M5');
      await page
        .getByRole('button', { name: t('dataEditor.delete') })
        .first()
        .click();
      await expect(page.getByTestId('data-editor-changes')).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
      await expectNoRawKeys(page);

      await page.getByRole('button', { name: t('dataEditor.preview') }).click();
      const review = page.getByTestId('data-editor-review');
      await expect(review.getByRole('note')).toBeVisible();
      // A delete is destructive: the text to type is the table's name (D-10).
      await review.getByLabel(t('dataEditor.review.type', { text: 'parts' })).fill('parts');
      expect(await seriousViolations(page)).toEqual([]);
      await expectNoRawKeys(page);

      await review.getByRole('button', { name: t('dataEditor.review.confirm') }).click();
      await expect(page.getByTestId('data-editor-execute')).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);

      await page.getByTestId('data-editor-execute').click();
      await expect(page.getByTestId('data-editor-error')).toContainText(
        t('problems.data_editor_conflict'),
      );
      expect(await seriousViolations(page)).toEqual([]);
      await expectNoRawKeys(page);
    });

    test(`${theme}, ${lang}: a refused table and the PROD lock pass axe`, async ({ page }) => {
      const t = (key: string, values: Record<string, string> = {}) => text(lang, key, values);
      await open(page, { refuse: 'data_editor_column_incomparable' }, theme, lang);
      await expect(page.getByTestId('data-editor-problem')).toHaveText(
        t('problems.data_editor_column_incomparable'),
      );
      expect(await seriousViolations(page)).toEqual([]);
      await expectNoRawKeys(page);

      await open(page, { locked: 'prod' }, theme, lang);
      await unlock(page, lang);
      await expect(page.getByTestId('data-editor-unlock').getByRole('alert')).toHaveText(
        t('problems.write_prod_locked'),
      );
      expect(await seriousViolations(page)).toEqual([]);
      await expectNoRawKeys(page);
    });
  }
}
