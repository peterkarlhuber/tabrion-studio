/**
 * STU-020 acceptance against a real `tabrion-studio` process (09 STU-020: "E2E: Favorit anlegen/öffnen, Historie ohne
 * Parameterwerte, Skript speichern/öffnen, Autocomplete aus Katalog"): a favorite made in the connection manager and
 * opened from the start page, the history of a query that ran with a parameter value (the value is nowhere on the
 * page and nowhere in Studio's state database), a script saved into the project folder and opened again, and
 * completion of a table's columns from the explorer's catalog. Under the strict CSP, with axe.
 *
 * Runs only when TABRION_STUDIO_E2E_PYTHON names a Python in which Studio is installed with its built frontend, like
 * e2e/editor-backend.spec.ts.
 */
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { test, expect, type Page } from '@playwright/test';

import { expectNoViolations, recordViolations, seriousViolations } from './support';

const python = process.env.TABRION_STUDIO_E2E_PYTHON;
test.skip(!python, 'set TABRION_STUDIO_E2E_PYTHON to run against a real tabrion-studio');
test.describe.configure({ mode: 'serial' });

const NO_KEYRING_ENV = { ...process.env, PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring' };
const LINK = /do not share\): (http:\/\/127\.0\.0\.1:\d+\/launch#\S+)/;
/** A parameter value that must never be stored (08 section 5: "Parameter nie in Historie"). */
const CANARY = `canary-${Date.now().toString(36)}-value`;

let studio: ChildProcess | undefined;
let root = '';
let database = '';
const links: string[] = [];

function args(): string[] {
  return [
    '-m',
    'tabrion_studio',
    join(root, 'project'),
    '--no-browser',
    '--print-url',
    '--state-dir',
    join(root, 'state'),
  ];
}

test.beforeAll(async () => {
  root = mkdtempSync(join(tmpdir(), 'studio-history-e2e-'));
  mkdirSync(join(root, 'project', 'sql'), { recursive: true });
  writeFileSync(join(root, 'project', 'sql', 'existing.sql'), 'select count(*) from parts;\n');
  database = join(root, 'parts.db');
  execFileSync(python as string, [
    '-I',
    '-c',
    [
      'import sqlite3, sys',
      'db = sqlite3.connect(sys.argv[1])',
      "db.execute('create table parts (id integer primary key, material text, weight real)')",
      "db.executemany('insert into parts values (?, ?, ?)', [(i, 'Aluminium' if i % 2 else 'Steel', i * 0.5) for i in range(1, 11)])",
      'db.commit()',
    ].join('\n'),
    database,
  ]);
  studio = spawn(python as string, args(), {
    stdio: ['ignore', 'pipe', 'inherit'],
    env: NO_KEYRING_ENV,
  });
  const first = await new Promise<string>((resolve, reject) => {
    let out = '';
    const timer = setTimeout(() => reject(new Error(`no sign-in link: ${out}`)), 30_000);
    studio?.stdout?.on('data', (chunk: Buffer) => {
      out += chunk.toString();
      const match = LINK.exec(out);
      if (match?.[1]) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
  });
  links.push(first);
});

test.afterAll(() => {
  studio?.kill('SIGTERM');
  rmSync(root, { recursive: true, force: true });
});

function newLink(): string {
  const out = execFileSync(python as string, args(), { encoding: 'utf-8', env: NO_KEYRING_ENV });
  const match = LINK.exec(out);
  if (!match?.[1]) throw new Error(`no sign-in link: ${out}`);
  return match[1];
}

async function signIn(page: Page): Promise<string[]> {
  const violations = await recordViolations(page);
  await page.goto(links.shift() ?? newLink());
  await expect(page.getByTestId('backend-status')).toContainText('Local server');
  return violations;
}

async function api(page: Page, method: string, path: string, body: unknown): Promise<number> {
  return page.evaluate(
    async ({ method, path, body }) => {
      const csrf = sessionStorage.getItem('tabrion-studio.csrf') ?? '';
      const response = await fetch(path, {
        method,
        headers: { 'X-Tabrion-CSRF': csrf, 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      return response.status;
    },
    { method, path, body },
  );
}

async function openEditor(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'SQL editor' }).click();
  await expect(page.locator('.cm-content')).toBeVisible();
  await expect(page.getByTestId('editor-profile')).toHaveValue(/e2e-parts/);
}

async function replaceText(page: Page, sql: string): Promise<void> {
  await page.locator('.cm-content').click();
  await page.keyboard.press('Control+A');
  await page.keyboard.press('Delete');
  await page.keyboard.insertText(sql);
}

test('setup: a SQLite profile', async ({ page }) => {
  await signIn(page);
  expect(
    await api(page, 'POST', '/api/profiles', {
      profile: { name: 'e2e-parts', engine: 'sqlite', environment: 'dev', path: database },
      digest: null,
      overwrite: false,
    }),
  ).toBe(201);
});

test('a favorite made in the connection manager opens from the start page', async ({ page }) => {
  const violations = await signIn(page);
  await page.getByRole('link', { name: 'Connections' }).click();
  const star = page.getByTestId('connection-favorite');
  await expect(star).toHaveAttribute('aria-pressed', 'false');
  await star.click();
  await expect(star).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('link', { name: 'Start' }).click();
  const favorites = page.getByTestId('start-favorites');
  await expect(favorites).toContainText('e2e-parts');
  expect(await seriousViolations(page)).toEqual([]);
  await favorites.getByTestId('quick-connect').click();
  await expect(page.getByTestId('connection-state')).toHaveText('open (read-only)');
  // Opened, so it is recent now too.
  await page.getByRole('link', { name: 'Start' }).click();
  await expect(page.getByTestId('start-recent')).toContainText('e2e-parts');
  await expectNoViolations(page, violations);
});

test('the history keeps the statement and its parameter names, never the value', async ({
  page,
}) => {
  const violations = await signIn(page);
  await openEditor(page);
  await replaceText(page, 'select id, material from parts where material = :material');
  await page.getByTestId('editor-run').click();
  await page.getByTestId('param-panel').getByRole('textbox', { name: ':material' }).fill(CANARY);
  await page.getByTestId('editor-run').click();
  await expect(page.getByTestId('result-status')).toContainText('0 rows');
  await page.keyboard.press('Control+h');
  await expect(page.getByRole('heading', { level: 1, name: 'Query history' })).toBeVisible();
  const entry = page.getByTestId('history-entry').first();
  await expect(entry.getByTestId('history-sql')).toHaveText(
    'select id, material from parts where material = :material',
  );
  await expect(entry.getByTestId('history-params')).toHaveText(
    'Parameters (values not kept): :material',
  );
  expect(await page.content()).not.toContain(CANARY);
  const answer = await page.evaluate(async () => (await fetch('/api/history')).text());
  expect(answer).not.toContain(CANARY);
  // Not in any file of Studio's state folder either (the database, its write-ahead log).
  for (const name of readdirSync(join(root, 'state'))) {
    const file = join(root, 'state', name);
    if (existsSync(file) && !name.endsWith('.lock')) {
      try {
        expect(readFileSync(file).includes(CANARY)).toBe(false);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'EISDIR') throw error;
      }
    }
  }
  for (const theme of ['Light', 'Dark']) {
    await page.getByTestId('open-palette').click();
    await page.keyboard.type(`theme ${theme}`);
    await page.keyboard.press('Enter');
    expect(await seriousViolations(page)).toEqual([]);
  }
  // "Open in editor" asks for the value again.
  await entry.getByTestId('history-open').click();
  await expect(page.locator('.cm-content')).toHaveText(
    'select id, material from parts where material = :material',
  );
  await expect(
    page.getByTestId('param-panel').getByRole('textbox', { name: ':material' }),
  ).toHaveValue('');
  await expectNoViolations(page, violations);
});

test('a script saved into the project folder opens again', async ({ page }) => {
  const violations = await signIn(page);
  await openEditor(page);
  await replaceText(page, 'select weight from parts order by id;\n');
  await page.keyboard.press('Control+s');
  const dialog = page.getByRole('dialog', { name: 'Save script' });
  await dialog.getByTestId('script-path').fill('sql/weights.sql');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByTestId('editor-notice')).toHaveText('Saved to sql/weights.sql.');
  expect(readFileSync(join(root, 'project', 'sql', 'weights.sql'), 'utf-8')).toBe(
    'select weight from parts order by id;\n',
  );
  // A path outside the project is refused by the page and by the server.
  await page.getByTestId('editor-new-tab').click();
  await page.keyboard.insertText('select 1');
  await page.keyboard.press('Control+s');
  await dialog.getByTestId('script-path').fill('../outside.sql');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(dialog.getByTestId('script-path')).toHaveAttribute('aria-invalid', 'true');
  expect(
    await api(page, 'PUT', '/api/scripts/file', {
      path: '../outside.sql',
      content: 'x',
      digest: null,
    }),
  ).toBe(400);
  expect(existsSync(join(root, 'outside.sql'))).toBe(false);
  await page.keyboard.press('Escape');
  // Open: the project's scripts, the saved one among them.
  await page.getByTestId('editor-open').click();
  const open = page.getByRole('dialog', { name: 'Open script' });
  await expect(open.getByTestId('script-item')).toHaveText(['sql/existing.sql', 'sql/weights.sql']);
  expect(await seriousViolations(page)).toEqual([]);
  await open.getByRole('button', { name: 'sql/existing.sql' }).click();
  await expect(page.getByTestId('editor-tab').last()).toContainText('existing');
  await expect(page.locator('.cm-content')).toHaveText('select count(*) from parts;');
  await expectNoViolations(page, violations);
});

test('a script with mixed line endings is saved byte for byte; Format runs under the strict CSP', async ({
  page,
}) => {
  const mixed = 'select id\r\nfrom parts\nwhere id = 1;\r\n';
  writeFileSync(join(root, 'project', 'sql', 'mixed.sql'), mixed);
  const violations = await signIn(page);
  await openEditor(page);
  await page.getByTestId('editor-open').click();
  await page
    .getByRole('dialog', { name: 'Open script' })
    .getByRole('button', { name: 'sql/mixed.sql' })
    .click();
  await expect(page.getByTestId('editor-tab').last()).toContainText('mixed');
  await page.locator('.cm-content').click();
  await page.keyboard.press('Control+End');
  await page.keyboard.insertText('-- checked');
  await page.keyboard.press('Control+s');
  await expect(page.getByTestId('editor-notice')).toHaveText('Saved to sql/mixed.sql.');
  expect(readFileSync(join(root, 'project', 'sql', 'mixed.sql'), 'utf-8')).toBe(
    `${mixed}-- checked`,
  );
  // Format loads sql-formatter on demand; the strict CSP allows it (no eval).
  await page.getByTestId('editor-new-tab').click();
  await page.keyboard.insertText('select id,material from parts where id=:id');
  await page.keyboard.press('Alt+Shift+F');
  await expect(page.getByTestId('editor-notice')).toContainText('Formatted.');
  await expect(page.locator('.cm-line').first()).toHaveText('SELECT');
  await expectNoViolations(page, violations);
});

test('completion: the columns of a table from the catalog of the open connection', async ({
  page,
}) => {
  const violations = await signIn(page);
  await openEditor(page);
  await replaceText(page, 'select 1');
  await page.getByTestId('editor-run').click();
  await expect(page.getByTestId('result-status')).toContainText('1 row');
  await page.locator('.cm-content').click();
  await page.keyboard.press('Control+End');
  await page.keyboard.press('Enter');
  await page.keyboard.type('select parts.');
  const list = page.locator('.cm-tooltip-autocomplete');
  await expect(list).toContainText('material');
  await expect(list).toContainText('weight');
  await page.keyboard.press('Escape');
  await page.keyboard.press('Enter');
  await page.keyboard.type('select * from par');
  await expect(list).toContainText('parts');
  await expectNoViolations(page, violations);
});
