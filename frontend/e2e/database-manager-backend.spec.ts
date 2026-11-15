/**
 * STU-XDM-2 end to end, with the Database Manager flag on in a test fixture only: a real `tabrion-studio` process
 * whose `--state-dir` holds `config/write-areas.toml` with `database_manager = true` (0600 in a 0700 folder, the one
 * source of the flag). Gate G-S4 is not granted; nothing outside this fixture turns the flag on.
 *
 * On a SQLite DEV connection: unlock its admin profile by the typed name, make a backup (private, in Studio's backup
 * folder, with its description), see it in the list with the size notice, preview a restore and restore it into a new
 * file next to the connection's file, confirmed by the typed name, under the strict Content-Security-Policy. axe in
 * the light and the dark theme. The PostgreSQL backup and restore (pg_dump, pg_restore, the lean restore role) are
 * proven against a server in tests/security/test_dbm_restore_roundtrip.py.
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

/** Run Python's sqlite3 on `path`: `script` reads `db`, and what it prints is returned. */
function sqlite(path: string, script: string): string {
  return execFileSync(
    python as string,
    ['-I', '-c', `import sqlite3, sys\ndb = sqlite3.connect(sys.argv[1])\n${script}`, path],
    { encoding: 'utf-8' },
  );
}

test.beforeAll(async () => {
  root = mkdtempSync(join(tmpdir(), 'studio-database-manager-e2e-'));
  mkdirSync(join(root, 'project'));
  mkdirSync(join(root, 'state'), { mode: 0o700 });
  const config = join(root, 'state', 'config');
  mkdirSync(config, { mode: 0o700 });
  chmodSync(config, 0o700);
  const flags = join(config, 'write-areas.toml');
  writeFileSync(flags, 'version = 1\n[areas]\ndatabase_manager = true\n', { mode: 0o600 });
  chmodSync(flags, 0o600);
  const data = join(root, 'data');
  mkdirSync(data, { mode: 0o700 });
  chmodSync(data, 0o700);
  database = join(data, 'shop.db');
  sqlite(
    database,
    [
      "db.execute('create table parts (id integer primary key, name text not null)')",
      "db.executemany('insert into parts values (?, ?)', [(1, 'bolt'), (2, 'nut')])",
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

/** Connect the read profile on the Connections page and return the Database Manager section. */
async function openManager(page: Page) {
  await page.getByRole('link', { name: 'Connections' }).click();
  const item = page.getByTestId('connection-item').filter({ hasText: 'e2e-shop' });
  await item.click();
  // The server keeps a connection an earlier test opened; connect only when none is open.
  const opened = (await api(page, 'GET', '/api/connections')).body as { connections: unknown[] };
  if (opened.connections.length === 0) {
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    await expect(page.getByTestId('connection-switcher')).toContainText('e2e-shop');
  }
  const section = page.getByTestId('database-manager');
  await expect(section).toBeVisible();
  return section;
}

test('setup: the flag comes from the fixture only; a read and an admin profile', async ({
  page,
}) => {
  await signIn(page);
  const areas = await api(page, 'GET', '/api/write/areas');
  expect(areas.body).toEqual({
    areas: { data_editor: false, schema_designer: false, database_manager: true },
  });
  const profiles = (await api(page, 'GET', '/api/profiles')).body as {
    user_file?: { digest?: string | null };
  };
  const created = await api(page, 'POST', '/api/profiles', {
    profile: { name: 'e2e-shop', engine: 'sqlite', environment: 'dev', path: database },
    digest: profiles.user_file?.digest ?? null,
    overwrite: false,
  });
  expect(created.status).toBe(201);
  const digest = (
    (await api(page, 'GET', '/api/profiles')).body as { user_file: { digest: string } }
  ).user_file.digest;
  const admin = await api(page, 'POST', '/api/write/profiles', {
    profile: { name: 'e2e-shop-admin', kind: 'admin', read_profile: 'e2e-shop' },
    digest,
    confirm_no_prod_credentials: true,
  });
  expect(admin.status).toBe(201);
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`unlock, back up, and restore into a new file (${theme})`, async ({ page }) => {
    const foreign = foreignRequests(page, () => base);
    const violations = await signIn(page, theme);
    const section = await openManager(page);
    await expect(section).toContainText('A PostgreSQL backup is not a complete backup');
    expect(await seriousViolations(page)).toEqual([]);

    const unlock = section.getByTestId('data-editor-unlock');
    await unlock.getByLabel('Type e2e-shop-admin to unlock').fill('e2e-shop-admin');
    await unlock.getByRole('button', { name: 'Unlock' }).click();
    await section.getByTestId('database-manager-backup').click();
    const made = section.getByTestId('database-manager-made');
    await expect(made).toContainText('made and checked');
    const file = /Backup (\S+\.sqlite) made/.exec((await made.textContent()) ?? '')?.[1] ?? '';
    expect(file).not.toBe('');
    await expect(section.getByTestId('database-manager-backups')).toContainText(file);
    await expect(section.getByTestId('database-manager-size')).toContainText(
      'Studio never deletes backups',
    );

    // The backup is private in Studio's backup folder, next to its description.
    const folder = join(root, 'state', 'config', 'backups', 'database-manager');
    expect(readdirSync(folder)).toContain(file);
    if (POSIX) {
      expect(lstatSync(folder).mode & 0o777).toBe(0o700);
      expect(lstatSync(join(folder, file)).mode & 0o777).toBe(0o600);
    }
    expect(await seriousViolations(page)).toEqual([]);

    await section.getByLabel('Backup', { exact: true }).selectOption(file);
    await section.getByTestId('database-manager-preview').click();
    const form = section.getByTestId('database-manager-restore');
    await expect(form).toBeVisible();
    const name = `copy-${theme}.db`;
    await form.getByLabel('New file name').fill(name);
    const run = form.getByRole('button', { name: 'Restore' });
    await expect(run).toBeDisabled();
    await expect(run).not.toBeFocused();
    await form.getByLabel(`Type ${name} to restore`).fill(name);
    expect(await seriousViolations(page)).toEqual([]);
    await run.click();
    await expect(section.getByTestId('database-manager-restored')).toContainText(
      `Restored into ${name}; its schema matches the backup.`,
    );
    const copy = join(root, 'data', name);
    expect(existsSync(copy)).toBe(true);
    expect(sqlite(copy, "print(db.execute('select count(*) from parts').fetchone()[0])")).toBe(
      '2\n',
    );
    await expectNoViolations(page, violations);
    expect(foreign).toEqual([]);
  });
}

test('the Database Manager flag does not open the write kind', async ({ page }) => {
  await signIn(page);
  const digest = (
    (await api(page, 'GET', '/api/profiles')).body as { user_file: { digest: string } }
  ).user_file.digest;
  // A write profile belongs to the Data Editor and the Schema Designer, whose flags are off (TX-01, TX-15).
  const write = await api(page, 'POST', '/api/write/profiles', {
    profile: { name: 'e2e-shop-w', kind: 'write', read_profile: 'e2e-shop' },
    digest,
    confirm_no_prod_credentials: true,
  });
  expect(write.status).toBe(405);
  // The page sends no tool, path or environment: the server reads them from its own files only.
  const listed = (await api(page, 'GET', '/api/write/database-manager/backups')).body as {
    tools: Record<string, { ok: boolean; reason: string | null }>;
  };
  expect(listed.tools.pg_dump).toEqual({ ok: false, reason: 'tool.not_configured', version: null });
  expect(JSON.stringify(listed)).not.toContain(root);
});
