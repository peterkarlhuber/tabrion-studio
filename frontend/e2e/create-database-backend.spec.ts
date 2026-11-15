/**
 * STU-CR-2 and STU-CR-3 end to end, with the Schema Designer flag on in a test fixture only: a real `tabrion-studio`
 * process whose `--state-dir` holds `config/write-areas.toml` with `schema_designer = true` (0600 in a 0700 folder,
 * the one source of the flag). Gate G-S3 is not granted; nothing outside this fixture turns the flag on.
 *
 * The "New database" wizard on the Connections page: a SQLite file in a private folder, confirmed by typing its name,
 * created 0600 under the strict Content-Security-Policy, then saved as a connection profile that connects; an existing
 * file refused and left as it was; a folder others may write refused; the PostgreSQL choice without an open
 * PostgreSQL connection says to connect first. axe in the light and the dark theme. The PostgreSQL creation itself is
 * proven against a server in tests/security/test_schema_designer_create_postgres.py.
 *
 * Runs only when TABRION_STUDIO_E2E_PYTHON names a Python in which Studio is installed with its built frontend
 * (`python scripts/build_frontend.py` and `pip install -e .`, or the wheel), like e2e/backend.spec.ts.
 */
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import {
  chmodSync,
  existsSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { test, expect, type Page } from '@playwright/test';

import {
  expectNoViolations,
  foreignRequests,
  recordViolations,
  seriousViolations,
  type Theme,
} from './support';

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
const POSIX = process.platform !== 'win32';

let studio: ChildProcess | undefined;
let root = '';
let base = '';
let folder = '';
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
  root = mkdtempSync(join(tmpdir(), 'studio-create-database-e2e-'));
  mkdirSync(join(root, 'project'));
  mkdirSync(join(root, 'state'), { mode: 0o700 });
  const config = join(root, 'state', 'config');
  mkdirSync(config, { mode: 0o700 });
  chmodSync(config, 0o700);
  const flags = join(config, 'write-areas.toml');
  writeFileSync(flags, 'version = 1\n[areas]\nschema_designer = true\n', { mode: 0o600 });
  chmodSync(flags, 0o600);
  folder = join(root, 'databases');
  mkdirSync(folder, { mode: 0o700 });
  chmodSync(folder, 0o700);
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

// Stop the server and wait for it before the folders go (the teardown race of STU-XDE-1).
test.afterAll(async () => {
  const proc = studio;
  if (proc && proc.exitCode === null && proc.signalCode === null) {
    const exited = new Promise<void>((resolve) => proc.once('exit', () => resolve()));
    proc.kill('SIGTERM');
    await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 10_000))]);
  }
  if (root) rmSync(root, { recursive: true, force: true });
});

function newLink(): string {
  const out = execFileSync(python as string, args(), { encoding: 'utf-8', env: NO_KEYRING_ENV });
  const match = LINK.exec(out);
  if (!match?.[1]) throw new Error(`no sign-in link: ${out}`);
  return match[1];
}

async function signIn(page: Page, theme: Theme = 'light'): Promise<string[]> {
  await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
  const violations = await recordViolations(page);
  await page.goto(links.shift() ?? newLink());
  await expect(page.getByTestId('backend-status')).toContainText('Local server');
  return violations;
}

async function openWizard(page: Page) {
  await page.getByRole('link', { name: 'Connections' }).click();
  const wizard = page.getByTestId('create-database');
  await expect(wizard).toBeVisible();
  return wizard;
}

/** A call to the API from the page, with its CSRF header: the status and the JSON body. */
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

/** The audit records of the run events, newest first. */
async function runRecords(
  page: Page,
): Promise<{ event: string; fields: Record<string, unknown> }[]> {
  const doc = (await api(page, 'GET', '/api/write/audit?limit=100')).body as {
    records: { event: string; fields: Record<string, unknown> }[];
  };
  return doc.records.filter((r) => r.event.startsWith('run.'));
}

test('the flag comes from the fixture only, and the wizard is shown', async ({ page }) => {
  await signIn(page);
  const areas = await api(page, 'GET', '/api/write/areas');
  expect(areas.body).toEqual({
    areas: { data_editor: false, schema_designer: true, database_manager: false },
  });
  await openWizard(page);
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`create a SQLite file, save its profile and connect (${theme})`, async ({ page }) => {
    const foreign = foreignRequests(page, () => base);
    const violations = await signIn(page, theme);
    const wizard = await openWizard(page);
    expect(await seriousViolations(page)).toEqual([]);

    const name = `inventory-${theme}.db`;
    const path = join(folder, name);
    await wizard.getByLabel('File path').fill(path);
    const create = wizard.getByRole('button', { name: 'Create file' });
    await expect(create).toBeDisabled();
    await expect(create).not.toBeFocused();
    await wizard.getByLabel(`Type ${name} to create the file`).fill(name);
    await expect(create).toBeEnabled();
    expect(await seriousViolations(page)).toEqual([]);
    await create.click();
    await expect(wizard.getByTestId('create-database-done')).toContainText(path);

    const info = lstatSync(path);
    expect(info.isFile() && !info.isSymbolicLink()).toBe(true);
    if (POSIX) expect(info.mode & 0o777).toBe(0o600);
    expect(readFileSync(path).subarray(0, 16).toString('latin1')).toBe('SQLite format 3\u0000');
    const runs = await runRecords(page);
    expect(runs[0]?.fields).toMatchObject({ result: 'committed' });
    expect(runs[1]?.fields).toMatchObject({
      action: 'schema_designer.create_sqlite',
      objects: [name],
    });
    // No folder in the audit log, only the file name.
    expect(JSON.stringify(runs)).not.toContain(folder);

    const profileName = `inventory-${theme}`;
    await expect(wizard.getByLabel('Profile name')).toHaveValue(profileName);
    await wizard.getByLabel('Environment').selectOption('test');
    expect(await seriousViolations(page)).toEqual([]);
    await wizard.getByRole('button', { name: 'Save profile' }).click();
    await expect(wizard.getByTestId('create-database-saved')).toContainText(profileName);
    const item = page.getByTestId('connection-item').filter({ hasText: profileName });
    await expect(item).toBeVisible();
    await item.click();
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    await expect(page.getByTestId('connection-switcher')).toContainText(profileName);
    await expect(page).toHaveTitle(`[TEST] ${profileName} – Tabrion Studio`);
    await expectNoViolations(page, violations);
    expect(foreign).toEqual([]);
  });
}

test('an existing file is refused and left as it was', async ({ page }) => {
  await signIn(page);
  const wizard = await openWizard(page);
  const path = join(folder, 'kept.db');
  writeFileSync(path, 'keep me', { mode: 0o600 });
  await wizard.getByLabel('File path').fill(path);
  await wizard.getByLabel('Type kept.db to create the file').fill('kept.db');
  await wizard.getByRole('button', { name: 'Create file' }).click();
  await expect(wizard.getByTestId('create-database-problem')).toContainText(
    'Studio never overwrites a file',
  );
  expect(readFileSync(path, 'utf-8')).toBe('keep me');
  expect(await seriousViolations(page)).toEqual([]);
});

test('a folder others may write, and a wrong file name ending, are refused', async ({ page }) => {
  test.skip(!POSIX, 'POSIX modes');
  await signIn(page);
  const wizard = await openWizard(page);
  const shared = join(root, 'shared');
  mkdirSync(shared, { mode: 0o700 });
  chmodSync(shared, 0o777);
  await wizard.getByLabel('File path').fill(join(shared, 'a.db'));
  await wizard.getByLabel('Type a.db to create the file').fill('a.db');
  await wizard.getByRole('button', { name: 'Create file' }).click();
  await expect(wizard.getByTestId('create-database-problem')).toContainText(
    'Studio does not create the file in this folder',
  );
  expect(readdirSync(shared)).toEqual([]);

  await wizard.getByLabel('File path').fill(join(folder, 'notes.txt'));
  await wizard.getByLabel('Type notes.txt to create the file').fill('notes.txt');
  await wizard.getByRole('button', { name: 'Create file' }).click();
  await expect(wizard.getByLabel('File path')).toHaveAttribute('aria-invalid', 'true');
  await expect(wizard).toContainText('The file name must end in .db, .sqlite or .sqlite3.');
  expect(existsSync(join(folder, 'notes.txt'))).toBe(false);
  expect(await seriousViolations(page)).toEqual([]);
});

test('the PostgreSQL choice asks for an open PostgreSQL connection first', async ({ page }) => {
  await signIn(page, 'dark');
  const wizard = await openWizard(page);
  await wizard.getByLabel('PostgreSQL database').check();
  await expect(wizard.getByTestId('create-postgres-none')).toBeVisible();
  expect(await seriousViolations(page)).toEqual([]);
  // Without an unlock the server refuses the route itself (W-9), whatever the page shows.
  const refused = await api(page, 'POST', '/api/write/schema-designer/create/postgresql', {
    connection_id: 'c_none',
    name: 'shop',
    typed: 'shop',
  });
  expect(refused.status).toBe(405);
  expect(refused.body).toMatchObject({ code: 'studio.write_locked' });
});
