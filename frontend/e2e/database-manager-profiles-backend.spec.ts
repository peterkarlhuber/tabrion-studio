/**
 * STU-XDM-5 end to end, with the Database Manager flag on in a test fixture only: a real `tabrion-studio` process
 * whose `--state-dir` holds `config/write-areas.toml` with `database_manager = true` (0600 in a 0700 folder, the one
 * source of the flag). Gate G-S4 is not granted; nothing outside this fixture turns the flag on.
 *
 * On a SQLite DEV connection with an admin profile coupled to it: see the pair, export the read profile to a new
 * profile file (preview, the typed file name; the admin profile is named and left out), then import that file under a
 * new name (preview, the name clash resolved by a new name, the typed file name). The imported profile is a read
 * profile without a secret. Under the strict Content-Security-Policy, with axe in the light and the dark theme. The
 * refusals (several hosts, links, foreign folders, malformed or oversized files, name clashes) are proven in
 * tests/security/test_dbm_profiles.py.
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
  root = mkdtempSync(join(tmpdir(), 'studio-dbm-profiles-e2e-'));
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
  // The imported copies (e2e-shop-light, ...) are listed too: the one named exactly e2e-shop.
  const item = page
    .getByTestId('connection-item')
    .filter({ has: page.getByText('e2e-shop', { exact: true }) });
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
  test(`export a read profile and import it under a new name (${theme})`, async ({ page }) => {
    const foreign = foreignRequests(page, () => base);
    const violations = await signIn(page, theme);
    const section = await openManager(page);
    const part = section.getByTestId('dbm-profiles');
    await expect(part.getByTestId('dbm-pair-e2e-shop')).toContainText('e2e-shop-admin (admin');
    expect(await seriousViolations(page)).toEqual([]);

    // Export: preview, then the typed file name. The admin profile is named and never written.
    const name = `shop-${theme}.tabrion-profile.toml`;
    const file = join(root, 'data', name);
    await part.getByTestId('dbm-export-pick-user:e2e-shop').check();
    await part.getByTestId('dbm-export-path').fill(file);
    await part.getByTestId('dbm-export-preview').click();
    const exportForm = part.getByTestId('dbm-export-form');
    await expect(exportForm).toContainText('e2e-shop (DEV), read only; no password');
    await expect(part.getByTestId('dbm-export-left-out')).toContainText('e2e-shop-admin (admin)');
    const exportRun = part.getByTestId('dbm-export-run');
    await expect(exportRun).toBeDisabled();
    await exportForm.getByLabel(`Type ${name} to confirm`).fill(name);
    expect(await seriousViolations(page)).toEqual([]);
    await exportRun.click();
    await expect(part.getByTestId('dbm-export-done')).toContainText(
      `Exported 1 profile to ${name}`,
    );
    expect(existsSync(file)).toBe(true);
    if (POSIX) expect(lstatSync(file).mode & 0o777).toBe(0o600);
    const written = readFileSync(file, 'utf-8');
    expect(written).toContain('e2e-shop');
    expect(written).not.toContain('e2e-shop-admin');
    expect(written).not.toContain('keyring');

    // Import: preview, the clash resolved by a new name, then the typed file name.
    const copy = `e2e-shop-${theme}`;
    await part.getByTestId('dbm-import-path').fill(file);
    await part.getByTestId('dbm-import-preview').click();
    const importForm = part.getByTestId('dbm-import-form');
    await expect(importForm.getByTestId('dbm-import-row-0')).toContainText(
      'You have a profile of this name',
    );
    await importForm.getByTestId('dbm-import-pick-0').check();
    const importRun = part.getByTestId('dbm-import-run');
    await importForm.getByLabel(`Type ${name} to confirm`).fill(name);
    // Under its own name the clash keeps the import closed: nothing is ever replaced.
    await expect(importRun).toBeDisabled();
    await importForm.getByTestId('dbm-import-as-0').fill(copy);
    expect(await seriousViolations(page)).toEqual([]);
    await importRun.click();
    await expect(part.getByTestId('dbm-import-done')).toContainText(
      `Imported 1 read profile: ${copy}.`,
    );
    await expect(part.getByTestId(`dbm-pair-${copy}`)).toContainText('no write or admin profile');

    // The imported profile is a read profile of the same file, with its environment and no secret.
    const listed = (await api(page, 'GET', '/api/profiles')).body as {
      profiles: {
        id: string;
        name: string;
        environment: string;
        path?: string;
        password?: unknown;
        secret: { source: string | null };
      }[];
    };
    const imported = listed.profiles.find((item) => item.name === copy);
    expect(imported?.id).toBe(`user:${copy}`);
    expect(imported?.environment).toBe('dev');
    expect(imported?.path).toBe(database);
    expect(imported?.password ?? null).toBeNull();
    expect(imported?.secret.source ?? null).toBeNull();
    const pairs = (await api(page, 'GET', '/api/write/profiles')).body as {
      write_profiles: { read_profile: string }[];
    };
    expect(pairs.write_profiles.filter((item) => item.read_profile === copy)).toEqual([]);
    await expectNoViolations(page, violations);
    expect(foreign).toEqual([]);
  });
}
