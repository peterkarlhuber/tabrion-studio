/**
 * STU-XDM-6 (GS4-24): the Database Manager's dialogs in a browser, against the harness's fake server
 * (`src/harness/databaseManager.ts`, scenario `database-manager`), so that the PostgreSQL parts run in Chromium,
 * Firefox and WebKit without a server or the `pg_dump` tools:
 *
 * - D-11, the backup before a destructive action: the delete says it backs up first, and its result names the backup;
 * - D-12, the restore: a preview first, on PostgreSQL only into a new database with the lean restore profile, the
 *   target's name typed;
 * - D-13, the delete: a preview first, the name typed, ending the other sessions never preselected and typed again;
 * - D-14, the role changes and the runtime-role wizard: a preview of the role's rights first, refusals and warnings,
 *   the name typed, a password sent once and only with the apply.
 *
 * In every dialog the destructive button is disabled until the typed text matches and never has the focus. axe finds
 * no serious or critical violation in light and dark, English and German, and no catalog key shows as raw text. The
 * real server's refusals are in tests/security/test_dbm_*.py; e2e/database-manager-backend.spec.ts runs the SQLite
 * backup, restore and delete against a real `tabrion-studio`. Gate G-S4 is not granted; the harness is never built
 * into the wheel.
 */
import { readFileSync } from 'node:fs';

import { test, expect, type Locator, type Page } from '@playwright/test';

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

interface Call {
  method: string;
  url: string;
  body: Record<string, unknown> | null;
}

const calls = (page: Page) =>
  page.evaluate(
    () => (window as unknown as { databaseManagerLog: { calls: Call[] } }).databaseManagerLog.calls,
  );

async function open(
  page: Page,
  options: Record<string, string> = {},
  theme: Theme = 'light',
  lang: Language = 'en',
): Promise<Locator> {
  const params = new URLSearchParams({ scenario: 'database-manager', theme, lang, ...options });
  await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
  await page.goto(`/harness.html?${params.toString()}`);
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await expect(page.locator('html')).toHaveAttribute('lang', lang);
  const section = page.getByTestId('database-manager');
  await expect(section).toBeVisible();
  return section;
}

/** No catalog key (`databaseManager.…`, `problems.…`, `dataEditor.…`) is shown instead of its translation. */
async function expectNoRawKeys(page: Page) {
  await expect(page.locator('main')).not.toContainText(
    /\b(databaseManager|dataEditor|problems)\.[a-z]/,
  );
}

async function unlock(section: Locator, lang: Language = 'en') {
  const form = section.getByTestId('data-editor-unlock');
  await expect(form).toBeVisible();
  const submit = form.getByRole('button', { name: text(lang, 'dataEditor.unlock.submit') });
  const field = form.getByLabel(text(lang, 'dataEditor.unlock.type', { name: 'shop-admin' }));
  await field.fill('shop-admin ');
  await expect(submit).toBeDisabled();
  await field.fill('shop-admin');
  await submit.click();
  await expect(section.getByTestId('database-manager-backup')).toBeVisible();
}

/** The typed confirmation: disabled until the text matches exactly, and never the focus. */
async function expectTypedGate(run: Locator, typed: Locator, name: string) {
  await expect(run).toBeDisabled();
  await expect(run).not.toBeFocused();
  for (const wrong of [name.toUpperCase(), `${name} `, name.slice(0, -1)]) {
    if (wrong === name) continue;
    await typed.fill(wrong);
    await expect(run).toBeDisabled();
  }
  await typed.fill(name);
  await expect(run).toBeEnabled();
  await expect(run).not.toBeFocused();
}

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as Language[]) {
    test(`every dialog of the area passes axe (${theme}, ${lang})`, async ({ page }) => {
      const section = await open(page, {}, theme, lang);
      await expect(section.getByTestId('database-manager-backups')).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
      await unlock(section, lang);
      expect(await seriousViolations(page)).toEqual([]);

      // D-12: the restore preview.
      await section.getByTestId('database-manager-preview').click();
      await expect(section.getByTestId('database-manager-restore')).toBeVisible();
      // D-13: the delete preview with other sessions.
      await section.getByTestId('database-manager-delete-name').fill('shop_old');
      await section.getByTestId('database-manager-delete-preview').click();
      await expect(section.getByTestId('database-manager-delete-sessions')).toBeVisible();
      // D-14: a role preview and the runtime-role wizard's preview.
      await section.getByTestId('database-manager-roles-role').fill('reporting');
      await section.getByTestId('database-manager-roles-preview').click();
      await expect(section.getByTestId('database-manager-roles-confirm')).toBeVisible();
      await section.getByTestId('database-manager-runtime-role-name').fill('app_rt');
      await section.getByTestId('database-manager-runtime-preview').click();
      await expect(section.getByTestId('database-manager-runtime-confirm')).toBeVisible();
      await expectNoRawKeys(page);
      expect(await seriousViolations(page)).toEqual([]);
    });
  }
}

test('D-12: a PostgreSQL restore goes only into a new database, as the restore profile, by the typed name', async ({
  page,
}) => {
  const section = await open(page);
  await unlock(section);
  await section.getByTestId('database-manager-preview').click();
  const form = section.getByTestId('database-manager-restore');
  await expect(section.getByTestId('database-manager-extensions')).toContainText('2');
  await expect(form).toContainText('only into a new database');
  await form.getByLabel('New database name').fill('shop_copy');
  await form.getByLabel(/Restore profile/).selectOption('write:shop-restore');
  const run = form.getByRole('button', { name: 'Restore' });
  await expectTypedGate(run, form.getByLabel('Type shop_copy to restore'), 'shop_copy');
  expect(await seriousViolations(page)).toEqual([]);
  await run.click();
  await expect(section.getByTestId('database-manager-restored')).toContainText('shop_copy');
  const sent = (await calls(page)).filter((call) => call.url.endsWith('/restore'));
  expect(sent).toHaveLength(1);
  // IDs, the name and the typed text only: never a path, a tool or an environment.
  expect(Object.keys(sent[0]?.body ?? {}).sort()).not.toContain('path');
  expect(JSON.stringify(sent[0]?.body)).not.toMatch(/pg_restore|\/data\//);
});

test('D-11 and D-13: the delete previews, backs up first, and offers ending sessions only typed again', async ({
  page,
}) => {
  const section = await open(page);
  await unlock(section);
  await section.getByTestId('database-manager-delete-name').fill('shop_old');
  await section.getByTestId('database-manager-delete-preview').click();
  await expect(section.getByTestId('database-manager-delete-sessions')).toContainText('2');
  const part = section.getByTestId('database-manager-delete');
  await expect(part).toContainText(
    'Studio makes a verified backup first and deletes nothing if that fails.',
  );
  const run = section.getByTestId('database-manager-delete-run');
  const force = section.getByTestId('database-manager-delete-force');
  await expect(force).not.toBeChecked();
  await expectTypedGate(run, section.getByTestId('database-manager-delete-typed'), 'shop_old');
  await force.check();
  // Ending the other sessions needs the name typed once more.
  await expect(run).toBeDisabled();
  await section.getByTestId('database-manager-delete-force-typed').fill('shop_old');
  await expect(run).toBeEnabled();
  await expect(run).not.toBeFocused();
  expect(await seriousViolations(page)).toEqual([]);
  await run.click();
  const deleted = section.getByTestId('database-manager-deleted');
  await expect(deleted).toContainText('20261115T101500Z-0123abcd.dump');
  const sent = (await calls(page)).filter((call) => call.url.endsWith('/delete'));
  expect(sent.map((call) => call.body?.force)).toEqual([true]);
});

test('D-13 on SQLite: only the connection’s own file, by its typed name; no name is sent', async ({
  page,
}) => {
  const section = await open(page, { engine: 'sqlite' });
  await unlock(section);
  await expect(section.getByTestId('database-manager-roles')).toHaveCount(0);
  await expect(section.getByTestId('database-manager-runtime-role')).toHaveCount(0);
  await section.getByTestId('database-manager-delete-preview').click();
  await expect(section.getByTestId('database-manager-delete-warning')).toBeVisible();
  await expect(section.getByTestId('database-manager-delete-force')).toHaveCount(0);
  const run = section.getByTestId('database-manager-delete-run');
  await expectTypedGate(run, section.getByTestId('database-manager-delete-typed'), 'notes.db');
  expect(await seriousViolations(page)).toEqual([]);
  await run.click();
  await expect(section.getByTestId('database-manager-deleted')).toContainText(
    '20261115T101600Z-89abcdef.sqlite',
  );
  const sent = (await calls(page)).filter((call) => call.url.endsWith('/delete'));
  expect(sent).toHaveLength(1);
  expect(sent[0]?.body?.name ?? null).toBeNull();
});

test('D-14: a new role is previewed, confirmed by its typed name, and its password goes once with the apply', async ({
  page,
}) => {
  const section = await open(page);
  await unlock(section);
  await expect(section.getByTestId('database-manager-roles-list')).toContainText('shop_admin');
  await section.getByTestId('database-manager-roles-role').fill('reporting');
  await section.getByTestId('database-manager-roles-login').check();
  await section.getByTestId('database-manager-roles-preview').click();
  await expect(section.getByTestId('database-manager-roles-confirm')).toBeVisible();
  const apply = section.getByTestId('database-manager-roles-apply');
  await section.getByTestId('database-manager-roles-password').fill('pw-canary');
  await expectTypedGate(apply, section.getByTestId('database-manager-roles-typed'), 'reporting');
  expect(await seriousViolations(page)).toEqual([]);
  await apply.click();
  await expect(section.getByTestId('database-manager-roles-done')).toBeVisible();
  const sent = await calls(page);
  const carrying = sent.filter((call) => JSON.stringify(call.body ?? {}).includes('pw-canary'));
  expect(carrying.map((call) => call.url)).toEqual(['/api/write/database-manager/roles/apply']);
});

test('D-14: a membership shows the databases not checked, a refusal blocks the apply', async ({
  page,
}) => {
  const section = await open(page, { refuse: 'role' });
  await unlock(section);
  await section.getByTestId('database-manager-roles-action').selectOption('grant_membership');
  await section.getByTestId('database-manager-roles-role').fill('readers');
  await section.getByTestId('database-manager-roles-member').fill('app');
  await section.getByTestId('database-manager-roles-preview').click();
  await expect(section.getByTestId('database-manager-roles-k2')).toContainText('archive');
  await expect(section.getByTestId('database-manager-roles-refusals')).toBeVisible();
  await expect(section.getByTestId('database-manager-roles-warnings')).toBeVisible();
  // A refused change has no confirmation at all: nothing to type, nothing to apply.
  await expect(section.getByTestId('database-manager-roles-apply')).toHaveCount(0);
  await expect(section.getByTestId('database-manager-roles-typed')).toHaveCount(0);
  await expectNoRawKeys(page);
  expect(await seriousViolations(page)).toEqual([]);
  expect((await calls(page)).some((call) => call.url.endsWith('/roles/apply'))).toBe(false);
});

test('D-14: the runtime-role wizard looks first, warns to run again after a migration, and needs the name', async ({
  page,
}) => {
  const section = await open(page);
  await unlock(section);
  const wizard = section.getByTestId('database-manager-runtime-role');
  await expect(wizard).toBeVisible();
  await section.getByTestId('database-manager-runtime-role-name').fill('app_rt');
  await section.getByTestId('database-manager-runtime-schema').fill('app');
  await section.getByTestId('database-manager-runtime-preview').click();
  await expect(section.getByTestId('database-manager-runtime-confirm')).toBeVisible();
  await expect(section.getByTestId('database-manager-runtime-warnings')).toContainText(
    /migration/i,
  );
  const apply = section.getByTestId('database-manager-runtime-apply');
  await section.getByTestId('database-manager-runtime-password').fill('rt-canary');
  await expectTypedGate(apply, section.getByTestId('database-manager-runtime-typed'), 'app_rt');
  expect(await seriousViolations(page)).toEqual([]);
  await apply.click();
  await expect(section.getByTestId('database-manager-runtime-done')).toBeVisible();
  const carrying = (await calls(page)).filter((call) =>
    JSON.stringify(call.body ?? {}).includes('rt-canary'),
  );
  expect(carrying.map((call) => call.url)).toEqual([
    '/api/write/database-manager/runtime-role/apply',
  ]);
});

test('the runtime-role wizard is not there without the Schema Designer flag', async ({ page }) => {
  const section = await open(page, { runtime: '0' });
  await unlock(section);
  await expect(section.getByTestId('database-manager-roles')).toBeVisible();
  await expect(section.getByTestId('database-manager-runtime-role')).toHaveCount(0);
});

test('an unlock that ended shows the unlock again with the reason', async ({ page }) => {
  const section = await open(page, { locked: '1' });
  await unlock(section);
  await section.getByTestId('database-manager-backup').click();
  await expect(section.getByTestId('data-editor-unlock')).toBeVisible();
  await expect(section.getByTestId('database-manager-problem')).toBeVisible();
  await expectNoRawKeys(page);
  expect(await seriousViolations(page)).toEqual([]);
});
