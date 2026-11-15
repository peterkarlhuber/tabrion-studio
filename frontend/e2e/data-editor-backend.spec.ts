/**
 * STU-026 end to end, with the Data Editor flag on in a test fixture only: a real `tabrion-studio` process whose
 * `--state-dir` holds `config/write-areas.toml` with `data_editor = true` (0600 in a 0700 folder, the one source of
 * the flag). Gate G-S2 is not granted; nothing outside this fixture turns the flag on.
 *
 * Unlock with the typed write profile name (D-03), change a NULL cell, add a row and delete one, preview, confirm by
 * typing the table's name (a delete is destructive, D-10) and execute once under the strict Content-Security-Policy;
 * the database holds exactly the changes. Then a row changed behind the page's back: the run rolls back, says so and
 * names no value. The changes never go to the query history or the drafts (N-3).
 *
 * Runs only when TABRION_STUDIO_E2E_PYTHON names a Python in which Studio is installed with its built frontend
 * (`python scripts/build_frontend.py` and `pip install -e .`, or the wheel), like e2e/backend.spec.ts.
 */
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import { chmodSync, mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { test, expect, type Page } from '@playwright/test';

import { expectNoViolations, foreignRequests, recordViolations } from './support';

const python = process.env.TABRION_STUDIO_E2E_PYTHON;
test.skip(!python, 'set TABRION_STUDIO_E2E_PYTHON to run against a real tabrion-studio');
test.describe.configure({ mode: 'serial' });

/** I-1 of stu-008-review.md: the real server never reaches the developer's keyring. No variable moves a folder. */
const NO_KEYRING_ENV: NodeJS.ProcessEnv = {
  ...process.env,
  PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring',
};
delete NO_KEYRING_ENV.TABRION_STUDIO_CONFIG_DIR;
delete NO_KEYRING_ENV.TABRION_STUDIO_STATE_DIR;
const LINK = /do not share\): (http:\/\/127(?:\.\d{1,3}){3}:\d+\/launch#\S+)/;
const CANARY = 'canary-e2e-5b1d';

let studio: ChildProcess | undefined;
let root = '';
let base = '';
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

/** Run Python's sqlite3 on the test database: `script` reads `db`, and what it prints is returned. */
function sqlite(script: string): string {
  return execFileSync(
    python as string,
    [
      '-I',
      '-c',
      `import json, sqlite3, sys\ndb = sqlite3.connect(sys.argv[1])\n${script}`,
      database,
    ],
    { encoding: 'utf-8' },
  );
}

test.beforeAll(async () => {
  root = mkdtempSync(join(tmpdir(), 'studio-data-editor-e2e-'));
  mkdirSync(join(root, 'project'));
  mkdirSync(join(root, 'state'), { mode: 0o700 });
  const config = join(root, 'state', 'config');
  mkdirSync(config, { mode: 0o700 });
  chmodSync(config, 0o700);
  const flags = join(config, 'write-areas.toml');
  writeFileSync(flags, 'version = 1\n[areas]\ndata_editor = true\n', { mode: 0o600 });
  chmodSync(flags, 0o600);
  database = join(root, 'parts.db');
  sqlite(
    [
      "db.execute('create table parts (id integer primary key, name text not null, qty integer)')",
      "db.executemany('insert into parts values (?, ?, ?)', [(1, 'bolt', 3), (2, 'nut', None), (3, 'washer', 7)])",
      'db.commit()',
    ].join('\n'),
  );
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
  base = new URL(first).origin;
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

/** A call to the API from the page, with its CSRF header: the status and the JSON body (null without one). */
async function api(
  page: Page,
  method: string,
  path: string,
  body?: unknown,
): Promise<{ status: number; body: unknown }> {
  return page.evaluate(
    async ({ method, path, body }) => {
      const csrf = sessionStorage.getItem('tabrion-studio.csrf') ?? '';
      const response = await fetch(path, {
        method,
        headers: { 'X-Tabrion-CSRF': csrf, 'Content-Type': 'application/json' },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      const text = await response.text();
      return { status: response.status, body: text === '' ? null : (JSON.parse(text) as unknown) };
    },
    { method, path, body },
  );
}

/** Open the explorer on the read profile, select the table and its "Edit data" tab. */
async function openEditor(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'Explorer' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Explorer' })).toBeVisible();
  await page.getByRole('combobox').first().selectOption('user:e2e-editor');
  await page.getByTestId('explorer-open').click();
  await page.getByRole('treeitem', { name: 'parts', exact: true }).click();
  await page.getByRole('tab', { name: 'Edit data' }).click();
}

/** The requests the page sends that are not GETs, with their bodies. */
function writes(page: Page): { path: string; body: string }[] {
  const seen: { path: string; body: string }[] = [];
  page.on('request', (request) => {
    if (request.method() !== 'GET') {
      seen.push({ path: new URL(request.url()).pathname, body: request.postData() ?? '' });
    }
  });
  return seen;
}

test('setup: the server reads the flag from the fixture; a read and a write profile', async ({
  page,
}) => {
  await signIn(page);
  const areas = await api(page, 'GET', '/api/write/areas');
  expect(areas.body).toMatchObject({ areas: { data_editor: true } });
  const profiles = (await api(page, 'GET', '/api/profiles')).body as {
    user_file?: { digest?: string | null };
  };
  const created = await api(page, 'POST', '/api/profiles', {
    profile: { name: 'e2e-editor', engine: 'sqlite', environment: 'dev', path: database },
    digest: profiles.user_file?.digest ?? null,
    overwrite: false,
  });
  expect(created.status).toBe(201);
  const digest = (
    (await api(page, 'GET', '/api/profiles')).body as { user_file: { digest: string } }
  ).user_file.digest;
  const write = await api(page, 'POST', '/api/write/profiles', {
    profile: { name: 'e2e-editor-w', kind: 'write', read_profile: 'e2e-editor' },
    digest,
    confirm_no_prod_credentials: true,
  });
  expect(write.status).toBe(201);
});

test('unlock, edit, add, delete, preview, confirm and execute once', async ({ page }) => {
  const foreign = foreignRequests(page, () => base);
  const violations = await signIn(page);
  const sent = writes(page);
  await openEditor(page);
  const unlock = page.getByTestId('data-editor-unlock');
  await expect(unlock).toBeVisible();
  await unlock.getByLabel('Type e2e-editor-w to unlock').fill('e2e-editor-w');
  await unlock.getByRole('button', { name: 'Unlock' }).click();
  await expect(page.getByTestId('data-editor-grid')).toBeVisible();

  // A NULL cell gets a number, a new row a canary name, the washer row is deleted.
  await page.getByRole('textbox', { name: 'Row 2, qty' }).fill('9');
  await page.getByRole('button', { name: 'Add row' }).click();
  await page.getByRole('textbox', { name: 'New row 1, name' }).fill(CANARY);
  await page
    .getByTestId('data-editor-grid')
    .locator('tr[data-row="2"]')
    .getByRole('button', { name: 'Delete row' })
    .click();
  await expect(page.getByTestId('data-editor-pending')).toHaveText('3 changes');
  await page.getByRole('button', { name: 'Preview' }).click();

  // A delete is destructive: the text to type is the table's name (D-10).
  await page.getByLabel('Type parts to confirm').fill('parts');
  await page.getByRole('button', { name: 'Confirm' }).click();
  await page.getByTestId('data-editor-execute').click();
  await expect(page.getByTestId('data-editor-saved')).toContainText(
    'Saved: 1 updated, 1 inserted, 1 deleted.',
  );
  await expect(page.getByRole('textbox', { name: 'Row 2, qty' })).toHaveValue('9');

  expect(
    JSON.parse(
      sqlite("print(json.dumps(db.execute('select * from parts order by id').fetchall()))"),
    ),
  ).toEqual([
    [1, 'bolt', 3],
    [2, 'nut', 9],
    [4, CANARY, null],
  ]);
  // N-3: the changes went to the plan only; no draft, no history entry holds them.
  expect(sent.filter((request) => request.body.includes(CANARY)).map((r) => r.path)).toEqual([
    '/api/write/plans',
  ]);
  expect(sent.filter((request) => /^\/api\/(drafts|history)/.test(request.path))).toEqual([]);
  const history = await api(page, 'GET', '/api/history?profile_id=user%3Ae2e-editor&limit=50');
  expect(history.status).toBe(200);
  expect(JSON.stringify(history.body)).not.toContain(CANARY);
  await expectNoViolations(page, violations);
  expect(foreign).toEqual([]);
});

test('a row changed behind the page: the run rolls back and names no value', async ({ page }) => {
  await signIn(page);
  await openEditor(page);
  // The unlock of the last test belongs to its connection; this one unlocks again.
  const unlock = page.getByTestId('data-editor-unlock');
  await unlock.getByLabel('Type e2e-editor-w to unlock').fill('e2e-editor-w');
  await unlock.getByRole('button', { name: 'Unlock' }).click();
  await expect(page.getByRole('textbox', { name: 'Row 1, qty' })).toHaveValue('3');
  sqlite('db.execute("update parts set name = \'bolt M4\' where id = 1")\ndb.commit()');
  await page.getByRole('textbox', { name: 'Row 1, qty' }).fill('42');
  await page.getByRole('button', { name: 'Preview' }).click();
  await page.getByLabel('Type e2e-editor-w to confirm').fill('e2e-editor-w');
  await page.getByRole('button', { name: 'Confirm' }).click();
  await page.getByTestId('data-editor-execute').click();
  const problem = page.getByRole('alert').filter({ hasText: 'Nothing was saved' });
  await expect(problem).toContainText('changed or deleted by someone else');
  await expect(problem).not.toContainText('bolt');
  await expect(problem).not.toContainText('42');
  expect(
    JSON.parse(
      sqlite("print(json.dumps(db.execute('select * from parts where id = 1').fetchall()))"),
    ),
  ).toEqual([[1, 'bolt M4', 3]]);
  await page.getByRole('button', { name: 'Reload the rows' }).click();
  await expect(page.getByRole('textbox', { name: 'Row 1, name' })).toHaveValue('bolt M4');
});
