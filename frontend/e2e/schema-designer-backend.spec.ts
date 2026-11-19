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
 * STU-CR-7: the focus after a confirmation and after a run (GS3-14: on the status, never on Run), and the read-only
 * schema comparison of STU-XSD-2 (a connection with itself, its plan preview empty) with axe in both themes.
 *
 * STU-XSD-1: the diagram of the draft: a table chosen with the keyboard, a foreign key drawn with the pointer and
 * applied through the table view's form (the same edit to the same route), the plan preview; axe and the CSP.
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

/** The middle of an element on the page (a handle of the diagram is 10 px wide). */
async function middle(locator: ReturnType<Page['locator']>): Promise<{ x: number; y: number }> {
  const box = await locator.boundingBox();
  if (box === null) throw new Error('not on the page');
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

// STU-XSD-1 (GS3-23, TX-31): the diagram of the draft. A table chosen with the keyboard shows its card from the table
// view (the focus moves there); a line drawn from a column to a column fills in that card's "New constraint" form,
// and only Apply sends it, as the same edit to the same route. Then the plan preview; nothing runs. axe and the CSP in
// each browser.
for (const theme of ['light', 'dark'] as Theme[]) {
  test(`the diagram: choose a table with the keyboard, draw a foreign key, read the plan (${theme})`, async ({
    page,
  }) => {
    const foreign = foreignRequests(page, () => base);
    const violations = await signIn(page, theme);
    const sent = writes(page);
    const designer = await openDesigner(page);
    const unlock = designer.getByTestId('data-editor-unlock');
    await unlock.getByLabel('Type e2e-designer-w to unlock').fill('e2e-designer-w');
    await unlock.getByRole('button', { name: 'Unlock' }).click();
    await designer.getByRole('button', { name: 'Start draft' }).click();
    await expect(designer.getByTestId('schema-designer-table')).toHaveCount(1);

    // A second table with a column to refer from, in the table view.
    await designer.getByRole('button', { name: 'Add table' }).click();
    let form = designer.getByTestId('schema-designer-form');
    await form.getByLabel('Table name').fill('bins');
    await form.getByLabel('First column', { exact: true }).fill('id');
    await form.getByLabel('Type', { exact: true }).fill('INTEGER');
    await form.getByRole('button', { name: 'Apply' }).click();
    const bins = designer.getByTestId('schema-designer-table').filter({ hasText: 'bins' });
    await bins.getByRole('button', { name: 'Add column' }).click();
    form = bins.getByTestId('schema-designer-form');
    await form.getByLabel('Column name').fill('part_id');
    await form.getByLabel('Type', { exact: true }).fill('INTEGER');
    await form.getByRole('button', { name: 'Apply' }).click();
    await expect(bins.getByRole('rowheader', { name: 'part_id' })).toBeVisible();

    // The diagram: both tables, their status, no card until one is chosen.
    const views = designer.getByRole('group', { name: 'Show the draft as' });
    await views.getByRole('button', { name: 'Diagram' }).click();
    const canvas = designer.getByTestId('schema-canvas');
    await expect(canvas.getByTestId('schema-canvas-box')).toHaveCount(2);
    await expect(canvas.getByRole('button', { name: 'Edit table bins new' })).toBeVisible();
    await expect(designer.getByTestId('schema-designer-table')).toHaveCount(0);
    expect(await seriousViolations(page)).toEqual([]);

    // The keyboard path: Tab reaches each table's name; Enter shows its card and moves the focus there.
    const partsName = canvas.getByRole('button', { name: 'Edit table parts' });
    await partsName.focus();
    await page.keyboard.press('Enter');
    const card = designer.getByTestId('schema-canvas-card');
    await expect(card).toBeFocused();
    await expect(partsName).toHaveAttribute('aria-pressed', 'true');
    await expect(card.getByRole('heading', { name: 'parts' })).toBeVisible();
    expect(await seriousViolations(page)).toEqual([]);

    // Draw a line from bins.part_id (the second row) to parts.id (the first row): the form opens filled in.
    const before = sent.length;
    // A node's id is its index (review I-1 of #148); the box carries the table's name.
    const node = (name: string) =>
      canvas.locator('.react-flow__node').filter({ has: page.locator(`[data-table="${name}"]`) });
    const from = node('bins').locator('[data-handleid="from:1"]');
    const to = node('parts').locator('[data-handleid="to:0"]');
    // The card took the focus (and the page scrolled to it): the canvas comes back into view for the pointer.
    await canvas.scrollIntoViewIfNeeded();
    const start = await middle(from);
    const end = await middle(to);
    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    await page.mouse.move((start.x + end.x) / 2, (start.y + end.y) / 2, { steps: 5 });
    await page.mouse.move(end.x, end.y, { steps: 5 });
    await page.mouse.up();
    form = card.getByTestId('schema-designer-form');
    await expect(form.getByRole('heading', { name: 'New constraint on bins' })).toBeVisible();
    await expect(form.getByLabel('Kind')).toHaveValue('foreign_key');
    await expect(form.getByLabel('Columns', { exact: true })).toHaveValue('part_id');
    await expect(form.getByLabel('Referred table')).toHaveValue('parts');
    await expect(form.getByLabel('Referred columns')).toHaveValue('id');
    // Drawing sent nothing.
    expect(sent.slice(before)).toEqual([]);
    expect(await seriousViolations(page)).toEqual([]);
    await form.getByRole('button', { name: 'Apply' }).click();
    await expect(
      card.getByRole('listitem').filter({ hasText: 'Foreign key (part_id) → parts (id)' }),
    ).toBeVisible();
    await expect(canvas.locator('.react-flow__edge')).toHaveCount(1);
    const drawn = sent.slice(before);
    expect(drawn).toHaveLength(1);
    expect(drawn[0]?.path).toBe('/api/write/schema-designer/draft/edits');
    expect((JSON.parse(drawn[0]?.body ?? '{}') as { edit: unknown }).edit).toEqual({
      op: 'add_constraint',
      table: 'bins',
      constraint: {
        kind: 'foreign_key',
        columns: ['part_id'],
        referred_table: 'parts',
        referred_columns: ['id'],
      },
    });

    // The plan preview, as from the table view; nothing runs.
    await designer.getByRole('button', { name: 'Show plan' }).click();
    const preview = designer.getByTestId('schema-designer-preview');
    await expect(preview.getByTestId('schema-designer-step').first()).toContainText('CREATE TABLE');
    await expect(preview).toContainText('REFERENCES');
    expect(await seriousViolations(page)).toEqual([]);
    expect(
      sqlite(
        'print(json.dumps([r[0] for r in db.execute("select name from sqlite_master where type = \'table\' order by name")]))',
      ).trim(),
    ).toBe('["parts"]');
    // Every request of the designer went to the table view's routes with their keys only.
    for (const write of sent.filter((s) => s.path.startsWith('/api/write/schema-designer/'))) {
      expect([
        '/api/write/schema-designer/draft',
        '/api/write/schema-designer/draft/view',
        '/api/write/schema-designer/draft/edits',
        '/api/write/schema-designer/draft/preview',
      ]).toContain(write.path);
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
  // GS3-14: the Confirm button is gone; the focus is on the confirmation's status. Neither the run nor the dry run
  // has the focus by default.
  await expect(form.getByTestId('schema-designer-confirmed')).toBeFocused();
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
  await expect(designer.getByTestId('schema-designer-outcome')).toBeFocused();
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
  await expect(outcome).toBeFocused();
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

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`compare a schema read-only and preview the plan; nothing runs (${theme})`, async ({
    page,
  }) => {
    const foreign = foreignRequests(page, () => base);
    const violations = await signIn(page, theme);
    const sent = writes(page);
    await openDesigner(page);
    const compare = page.getByTestId('schema-compare');
    await expect(compare).toBeVisible();
    await expect(compare.getByTestId('schema-compare-actual-connection')).toContainText(
      'e2e-designer (DEV)',
    );
    await compare.getByTestId('schema-compare-plan').check();
    await compare.getByRole('button', { name: 'Compare', exact: true }).click();
    const result = compare.getByTestId('schema-compare-result');
    await expect(result).toHaveAttribute('data-verdict', 'same');
    await expect(compare.getByTestId('schema-compare-verdict')).toContainText(
      'The schemas are the same.',
    );
    await expect(result).toContainText('The compared side already matches the reference');
    expect(await seriousViolations(page)).toEqual([]);
    // Only the comparison was sent: no plan confirmation, no run.
    const posted = sent.map((s) => s.path).filter((path) => path.includes('/schema-designer/'));
    expect(posted).toEqual(['/api/write/schema-designer/compare']);
    await expectNoViolations(page, violations);
    expect(foreign).toEqual([]);
  });
}
