/**
 * STU-CR-4 end to end, with the Schema Designer flag on in a test fixture only: a real `tabrion-studio` process whose
 * `--state-dir` holds `config/write-areas.toml` with `schema_designer = true` (0600 in a 0700 folder, the one source
 * of the flag). Gate G-S3 is not granted; nothing outside this fixture turns the flag on.
 *
 * The designer's table view on the Connections page: unlock the SQLite connection's write profile, start a draft,
 * add a column and an index through the forms, see a refused edit with its text, and read the plan preview with its
 * SQL and hash under the strict Content-Security-Policy. The page sends edit operations only (never a model or a
 * plan), nothing runs (the file keeps its tables and columns), and the audit log holds no run. axe in the light and
 * the dark theme.
 *
 * STU-CR-5: drop a column, read the preview's warnings, confirm the plan (typed profile and table names, the ticked
 * data-changing step; the backup kept), try it as a dry run (rolled back: the file is unchanged), preview and confirm
 * again and run it: the column is gone, the backup file is in Studio's backup folder (0600) with the old column, and
 * the audit log holds the dry run and the run. The page sends the recorded hash and what was typed, never a plan.
 *
 * Runs only when TABRION_STUDIO_E2E_PYTHON names a Python in which Studio is installed with its built frontend
 * (`python scripts/build_frontend.py` and `pip install -e .`, or the wheel), like e2e/backend.spec.ts.
 */
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import {
  chmodSync,
  mkdtempSync,
  mkdirSync,
  readdirSync,
  rmSync,
  statSync,
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
  root = mkdtempSync(join(tmpdir(), 'studio-schema-designer-e2e-'));
  mkdirSync(join(root, 'project'));
  mkdirSync(join(root, 'state'), { mode: 0o700 });
  const config = join(root, 'state', 'config');
  mkdirSync(config, { mode: 0o700 });
  chmodSync(config, 0o700);
  const flags = join(config, 'write-areas.toml');
  writeFileSync(flags, 'version = 1\n[areas]\nschema_designer = true\n', { mode: 0o600 });
  chmodSync(flags, 0o600);
  database = join(root, 'parts.db');
  sqlite(
    [
      "db.execute('create table parts (id integer primary key, name text not null)')",
      'db.execute("insert into parts values (1, \'bolt\')")',
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

/** Connect the read profile on the Connections page, then show the page again so the designer sees the connection. */
async function openDesigner(page: Page) {
  await page.getByRole('link', { name: 'Connections' }).click();
  const item = page.getByTestId('connection-item').filter({ hasText: 'e2e-designer' });
  await item.click();
  // The connection of an earlier test may still be open (connections belong to the server, not the session).
  const connect = page.getByRole('button', { name: 'Connect', exact: true });
  const disconnect = page.getByRole('button', { name: 'Disconnect', exact: true });
  await expect(connect.or(disconnect)).toBeVisible();
  if (await connect.isVisible()) await connect.click();
  await expect(disconnect).toBeVisible();
  await page.getByRole('link', { name: 'Explorer' }).click();
  await page.getByRole('link', { name: 'Connections' }).click();
  const designer = page.getByTestId('schema-designer');
  await expect(designer).toBeVisible();
  return designer;
}

test('setup: the flag comes from the fixture; a read and a write profile', async ({ page }) => {
  await signIn(page);
  const areas = await api(page, 'GET', '/api/write/areas');
  expect(areas.body).toEqual({
    areas: { data_editor: false, schema_designer: true, database_manager: false },
  });
  const profiles = (await api(page, 'GET', '/api/profiles')).body as {
    user_file?: { digest?: string | null };
  };
  const created = await api(page, 'POST', '/api/profiles', {
    profile: { name: 'e2e-designer', engine: 'sqlite', environment: 'dev', path: database },
    digest: profiles.user_file?.digest ?? null,
    overwrite: false,
  });
  expect(created.status).toBe(201);
  const digest = (
    (await api(page, 'GET', '/api/profiles')).body as { user_file: { digest: string } }
  ).user_file.digest;
  const write = await api(page, 'POST', '/api/write/profiles', {
    profile: { name: 'e2e-designer-w', kind: 'write', read_profile: 'e2e-designer' },
    digest,
    confirm_no_prod_credentials: true,
  });
  expect(write.status).toBe(201);
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`design a change and read its plan; nothing runs (${theme})`, async ({ page }) => {
    const foreign = foreignRequests(page, () => base);
    const violations = await signIn(page, theme);
    const sent = writes(page);
    const designer = await openDesigner(page);

    // Each test signs in anew: a new session has neither the unlock nor the draft of the last one (both are bound to
    // the session).
    const unlock = designer.getByTestId('data-editor-unlock');
    await unlock.getByLabel('Type e2e-designer-w to unlock').fill('e2e-designer-w');
    await unlock.getByRole('button', { name: 'Unlock' }).click();
    const startForm = designer.getByTestId('schema-designer-start');
    await expect(startForm).toBeVisible();
    expect(await seriousViolations(page)).toEqual([]);
    await startForm.getByRole('button', { name: 'Start draft' }).click();
    const parts = designer.getByTestId('schema-designer-table').filter({ hasText: 'parts' });
    await expect(parts).toBeVisible();
    expect(await seriousViolations(page)).toEqual([]);

    const column = `qty_${theme}`;
    await parts.getByRole('button', { name: 'Add column' }).click();
    const form = parts.getByTestId('schema-designer-form');
    await form.getByLabel('Column name').fill(column);
    await form.getByLabel('Type', { exact: true }).fill('INTEGER');
    await form.getByLabel('Default').fill('0');
    expect(await seriousViolations(page)).toEqual([]);
    await form.getByRole('button', { name: 'Apply' }).click();
    await expect(parts.getByRole('rowheader', { name: column })).toBeVisible();

    // A refused edit: an unknown type, with tabrion's reason in the page's own words.
    await parts.getByRole('button', { name: 'Add column' }).click();
    await form.getByLabel('Column name').fill('bad');
    await form.getByLabel('Type', { exact: true }).fill('NO SUCH TYPE');
    await form.getByRole('button', { name: 'Apply' }).click();
    await expect(form.getByTestId('schema-designer-plan-issues')).toContainText(
      'Tabrion does not know this type for the database.',
    );
    expect(await seriousViolations(page)).toEqual([]);
    await form.getByRole('button', { name: 'Cancel' }).click();

    await designer.getByRole('button', { name: 'Show plan' }).click();
    const preview = designer.getByTestId('schema-designer-preview');
    await expect(preview).toContainText('Nothing runs until you confirm the plan and run it.');
    await expect(preview.getByTestId('schema-designer-plan-hash')).toContainText('sha256:');
    await expect(preview.getByTestId('schema-designer-step').first()).toContainText(
      `ALTER TABLE parts ADD COLUMN ${column} INTEGER DEFAULT (0)`,
    );
    expect(await seriousViolations(page)).toEqual([]);

    // Nothing ran: the file keeps its columns, and the audit log holds no run.
    expect(
      sqlite("print(json.dumps([r[1] for r in db.execute('pragma table_info(parts)')]))").trim(),
    ).toBe('["id", "name"]');
    const audit = (await api(page, 'GET', '/api/write/audit?limit=100')).body as {
      records: { event: string }[];
    };
    expect(audit.records.filter((r) => r.event.startsWith('run.'))).toEqual([]);
    // The page sent edit operations only: never a model, a snapshot or a plan (GS3-09).
    const designerWrites = sent.filter((s) => s.path.startsWith('/api/write/schema-designer/'));
    expect(designerWrites.length).toBeGreaterThan(0);
    for (const write of designerWrites) {
      const body = JSON.parse(write.body) as Record<string, unknown>;
      expect(
        Object.keys(body).every((key) =>
          ['connection_id', 'schema', 'revision', 'edit'].includes(key),
        ),
      ).toBe(true);
    }
    await designer.getByRole('button', { name: 'Discard draft' }).click();
    await expect(designer.getByTestId('schema-designer-start')).toBeVisible();
    await expectNoViolations(page, violations);
    expect(foreign).toEqual([]);
  });
}

/** Type and tick what the confirmation asks (the names shown in each label), keep the backup, and confirm. */
async function confirm(preview: ReturnType<Page['getByTestId']>) {
  const form = preview.getByTestId('schema-designer-run');
  await form.getByLabel('Type the write profile name: e2e-designer-w').fill('e2e-designer-w');
  await form.getByLabel('Type the table name: parts').fill('parts');
  for (const step of await form.getByTestId('schema-designer-run-step').all()) await step.check();
  await expect(form.getByTestId('schema-designer-run-backup')).toContainText(
    'A copy of the database file is made before the run.',
  );
  await form.getByRole('button', { name: 'Confirm' }).click();
  await expect(form.getByTestId('schema-designer-confirmed')).toBeVisible();
  // Neither the run nor the dry run has the focus by default.
  await expect(form.getByRole('button', { name: 'Run the plan' })).not.toBeFocused();
  await expect(form.getByRole('button', { name: 'Dry run' })).not.toBeFocused();
  return form;
}

test('drop a column: confirm, dry run, then run with its backup (light)', async ({ page }) => {
  const foreign = foreignRequests(page, () => base);
  const violations = await signIn(page);
  const sent = writes(page);
  const designer = await openDesigner(page);
  const unlock = designer.getByTestId('data-editor-unlock');
  await unlock.getByLabel('Type e2e-designer-w to unlock').fill('e2e-designer-w');
  await unlock.getByRole('button', { name: 'Unlock' }).click();
  await designer.getByRole('button', { name: 'Start draft' }).click();
  const parts = designer.getByTestId('schema-designer-table').filter({ hasText: 'parts' });
  await parts.getByRole('button', { name: 'Drop name', exact: true }).click();
  await expect(parts.getByRole('rowheader', { name: 'name' })).toHaveCount(0);

  await designer.getByRole('button', { name: 'Show plan' }).click();
  let preview = designer.getByTestId('schema-designer-preview');
  await expect(preview.getByTestId('schema-designer-warnings')).toContainText(
    'The draft does not show views, triggers',
  );
  let form = await confirm(preview);
  expect(await seriousViolations(page)).toEqual([]);
  await form.getByRole('button', { name: 'Dry run' }).click();
  await expect(designer.getByTestId('schema-designer-outcome')).toContainText('rolled back');
  expect(
    sqlite("print(json.dumps([r[1] for r in db.execute('pragma table_info(parts)')]))").trim(),
  ).toBe('["id", "name"]');

  // The dry run used the confirmation up: preview and confirm again, then run.
  await designer.getByRole('button', { name: 'Show plan' }).click();
  preview = designer.getByTestId('schema-designer-preview');
  form = await confirm(preview);
  await form.getByRole('button', { name: 'Run the plan' }).click();
  const outcome = designer.getByTestId('schema-designer-outcome');
  await expect(outcome).toContainText('run and committed');
  await expect(outcome.getByTestId('schema-designer-backup-path')).toContainText('schema-designer');
  expect(await seriousViolations(page)).toEqual([]);
  expect(
    sqlite("print(json.dumps([r[1] for r in db.execute('pragma table_info(parts)')]))").trim(),
  ).toBe('["id"]');

  // The backup: one file in Studio's backup folder, owner-only, with the dropped column still in it.
  const folder = join(root, 'state', 'config', 'backups', 'schema-designer');
  const files = readdirSync(folder);
  expect(files).toHaveLength(1);
  expect(statSync(folder).mode & 0o777).toBe(0o700);
  expect(statSync(join(folder, files[0] as string)).mode & 0o777).toBe(0o600);
  const kept = execFileSync(
    python as string,
    [
      '-I',
      '-c',
      "import json, sqlite3, sys\nprint(json.dumps([r[1] for r in sqlite3.connect(sys.argv[1]).execute('pragma table_info(parts)')]))",
      join(folder, files[0] as string),
    ],
    { encoding: 'utf-8' },
  ).trim();
  expect(kept).toBe('["id", "name"]');

  const audit = (await api(page, 'GET', '/api/write/audit?limit=100')).body as {
    records: { event: string; fields: Record<string, unknown> }[];
  };
  const runs = audit.records.filter((r) => r.event === 'run.start');
  expect(runs.map((r) => r.fields.action).sort()).toEqual([
    'schema_designer.dry_run',
    'schema_designer.run',
  ]);
  // The page sent the recorded hash and what was typed and ticked: never a plan, a model or an environment.
  for (const write of sent.filter((s) => s.path.startsWith('/api/write/schema-designer/plan/'))) {
    const body = JSON.parse(write.body) as Record<string, unknown>;
    expect(
      Object.keys(body).every((key) =>
        [
          'connection_id',
          'plan_hash',
          'typed',
          'tables',
          'steps',
          'locking',
          'backup',
          'dry_run',
        ].includes(key),
      ),
    ).toBe(true);
  }
  await expectNoViolations(page, violations);
  expect(foreign).toEqual([]);
});
