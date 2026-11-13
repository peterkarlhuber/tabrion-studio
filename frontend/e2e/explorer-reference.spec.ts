/**
 * STU-010 acceptance: the explorer against both reference applications of tabrion (`examples/parts`, Alembic, and
 * `examples/measurements`, tabrion.sqlite migrations), in the built frontend of a real `tabrion-studio` process.
 *
 * Runs only when TABRION_STUDIO_E2E_PYTHON names a Python with Studio installed (as for backend.spec.ts) and
 * TABRION_STUDIO_E2E_EXAMPLES names tabrion's `examples` folder; the PostgreSQL part also needs
 * TABRION_STUDIO_E2E_PG_PARTS and TABRION_STUDIO_E2E_PG_MEASUREMENTS (URLs of two empty databases the test may fill,
 * as their owner), TABRION_STUDIO_E2E_PG_USER (the role Studio reads with; the URLs' user when unset) and, for a
 * server with password authentication, TABRION_STUDIO_E2E_PG_PASSWORD (that role's password). A spec of
 * its own, so that backend.spec.ts, which CI requires to run without a skip, stays complete without these
 * variables. CI (job `e2e`) sets TABRION_STUDIO_E2E_PYTHON and TABRION_STUDIO_E2E_EXAMPLES, so the SQLite part runs
 * there; it sets no PostgreSQL URL, so the PostgreSQL part does not run in CI (TD-041).
 */
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { test, expect, type Page } from '@playwright/test';

import { expectNoViolations, recordViolations, seriousViolations } from './support';

const python = process.env.TABRION_STUDIO_E2E_PYTHON;
const examples = process.env.TABRION_STUDIO_E2E_EXAMPLES;
const pgParts = process.env.TABRION_STUDIO_E2E_PG_PARTS;
const pgMeasurements = process.env.TABRION_STUDIO_E2E_PG_MEASUREMENTS;
test.skip(
  !python || !examples,
  'set TABRION_STUDIO_E2E_PYTHON and TABRION_STUDIO_E2E_EXAMPLES (tabrion/examples) to run',
);

/** The real server never reaches the developer's keyring (I-1 of stu-008-review.md). */
const NO_KEYRING_ENV = { ...process.env, PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring' };

let studio: ChildProcess | undefined;
let root = '';
let link = '';

test.beforeAll(async () => {
  root = mkdtempSync(join(tmpdir(), 'studio-e2e-ra-'));
  mkdirSync(join(root, 'project'));
  studio = spawn(
    python as string,
    [
      '-m',
      'tabrion_studio',
      join(root, 'project'),
      '--no-browser',
      '--print-url',
      '--state-dir',
      join(root, 'state'),
    ],
    { stdio: ['ignore', 'pipe', 'inherit'], env: NO_KEYRING_ENV },
  );
  link = await new Promise<string>((resolve, reject) => {
    let out = '';
    const timer = setTimeout(() => reject(new Error(`no sign-in link: ${out}`)), 30_000);
    studio?.stdout?.on('data', (chunk: Buffer) => {
      out += chunk.toString();
      const match = /do not share\): (http:\/\/127\.0\.0\.1:\d+\/launch#\S+)/.exec(out);
      if (match?.[1]) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
  });
});

test.afterAll(() => {
  studio?.kill('SIGTERM');
  if (root) rmSync(root, { recursive: true, force: true });
});

async function signIn(page: Page): Promise<string[]> {
  const violations = await recordViolations(page);
  await page.goto(link);
  await expect(page.getByTestId('backend-status')).toContainText('Local server');
  return violations;
}

/** Run a reference application's own code to create its schema (the test's setup, not Studio). */
function referenceApp(app: 'parts' | 'measurements', code: string, args: string[] = []): void {
  execFileSync(python as string, ['-c', code, ...args], {
    encoding: 'utf-8',
    env: { ...NO_KEYRING_ENV, PYTHONPATH: join(examples as string, app) },
    cwd: root,
  });
}

const PARTS_SETUP =
  'import sys\nfrom parts import open_database, upgrade, require_current\n' +
  'db = open_database(sys.argv[1]); upgrade(db); require_current(db); db.close()';
const MEASUREMENTS_SQLITE_SETUP =
  'import sys\nfrom measurements import open_database\n' +
  'open_database(sys.argv[1], backup_dir=sys.argv[2]).close()';
const MEASUREMENTS_PG_SETUP =
  'import sys\nfrom measurements.upstream import create_upstream, open_upstream\n' +
  'db = open_upstream(sys.argv[1]); create_upstream(db); db.close()';

/** Save a user profile through the API, as the connection manager does (STU-008). */
async function addProfile(page: Page, profile: Record<string, unknown>): Promise<void> {
  const status = await page.evaluate(async (body) => {
    const csrf = sessionStorage.getItem('tabrion-studio.csrf') ?? '';
    const headers = { 'X-Tabrion-CSRF': csrf, 'Content-Type': 'application/json' };
    const list = await (await fetch('/api/profiles', { headers })).json();
    const response = await fetch('/api/profiles', {
      method: 'POST',
      headers,
      body: JSON.stringify({ profile: body, digest: list.user_file.digest, overwrite: false }),
    });
    return response.status;
  }, profile);
  expect(status).toBe(201);
}

async function openProfile(page: Page, name: string): Promise<void> {
  await page.getByLabel('Connection profile').selectOption({ label: `${name} (DEV)` });
  await page.getByTestId('explorer-open').click();
  await expect(page.getByTestId('explorer-message')).toContainText('Catalog read:');
}

/** The column names of the selected object (the page keeps the last tab, so this opens Columns first). */
async function columns(page: Page): Promise<string[]> {
  await page.getByRole('tab', { name: 'Columns' }).click();
  await expect(page.getByTestId('detail-column').first()).toBeVisible();
  return page.getByTestId('detail-column').locator('th code').allTextContents();
}

test('STU-010: the explorer against the parts and measurements reference apps', async ({
  page,
}) => {
  test.setTimeout(120_000);
  const parts = join(root, 'parts.db');
  const measurements = join(root, 'measurements.db');
  referenceApp('parts', PARTS_SETUP, [`sqlite:///${parts}`]);
  referenceApp('measurements', MEASUREMENTS_SQLITE_SETUP, [measurements, join(root, 'backups')]);
  const violations = await signIn(page);
  await addProfile(page, { name: 'ra-parts', engine: 'sqlite', path: parts, environment: 'dev' });
  await addProfile(page, {
    name: 'ra-measurements',
    engine: 'sqlite',
    path: measurements,
    environment: 'dev',
  });
  await page.getByRole('link', { name: 'Explorer' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Explorer' })).toBeVisible();

  // Parts (Alembic, SQLite): the four tables and alembic_version, the keys of `part` in and out.
  await openProfile(page, 'ra-parts');
  const tree = page.getByRole('tree');
  await expect(tree.getByRole('treeitem', { level: 2 })).toHaveText([
    'alembic_version',
    'material',
    'part',
    'part_relation',
    'property',
  ]);
  await tree.getByRole('treeitem', { name: 'part', exact: true }).click();
  expect(await columns(page)).toEqual([
    'id',
    'part_number',
    'name',
    'material_id',
    'status',
    'version',
    'created_at',
    'updated_at',
  ]);
  await page.getByRole('tab', { name: 'Constraints' }).click();
  await expect(page.getByTestId('detail-pk')).toHaveText('pk_part: id');
  await expect(page.getByTestId('detail-fk')).toHaveText(
    'fk_part_material: material_id → main.material (id)',
  );
  await expect(page.getByTestId('detail-unique')).toHaveText('uq_part_part_number: part_number');
  await expect(page.getByTestId('detail-check')).toHaveCount(4);
  await page.getByRole('tab', { name: 'Relationships' }).click();
  await expect(page.getByTestId('detail-referenced-by')).toHaveText([
    'main.part_relation (assembly_id) → id',
    'main.part_relation (component_id) → id',
    'main.property (part_id) → id',
  ]);
  await page.getByRole('tab', { name: 'Indexes' }).click();
  await expect(page.getByTestId('detail-index').first()).toContainText('ix_part_material_id');
  await page.getByRole('tab', { name: 'SQLite' }).click();
  await expect(page.getByTestId('detail-sqlite')).toContainText('WITHOUT ROWIDNo');
  await page.getByRole('tab', { name: 'Relationships' }).click();
  await page.getByRole('button', { name: 'Go to main.part_relation' }).first().click();
  await expect(tree.getByRole('treeitem', { name: 'part_relation' })).toBeFocused();
  expect(await columns(page)).toEqual(['assembly_id', 'component_id', 'quantity']);
  await tree.getByRole('treeitem', { name: 'Database file' }).click();
  await expect(page.getByTestId('detail-file')).toContainText('journal_mode');
  if (process.platform !== 'win32') {
    await expect(page.getByTestId('detail-file')).toContainText('File permissions0600');
  }
  expect(await seriousViolations(page)).toEqual([]);

  // Measurements (tabrion.sqlite, user_version 3): measurement and transfer_mark, the natural key and the index.
  await openProfile(page, 'ra-measurements');
  await expect(tree.getByRole('treeitem', { level: 2 })).toHaveText([
    'measurement',
    'transfer_mark',
  ]);
  await tree.getByRole('treeitem', { name: 'measurement', exact: true }).click();
  expect(await columns(page)).toEqual([
    'seq',
    'device_id',
    'channel',
    'ts_utc',
    'value',
    'unit',
    'quality',
  ]);
  await page.getByRole('tab', { name: 'Indexes' }).click();
  await expect(
    page.getByTestId('detail-index').filter({ hasText: 'measurement_ts' }),
  ).toContainText('ts_utc');
  await tree.getByRole('treeitem', { name: 'Database file' }).click();
  await expect(page.getByTestId('detail-file')).toContainText('user_version3');
  await page.getByTestId('explorer-filter').fill('trans');
  await expect(tree.getByRole('treeitem')).toHaveText(['Tables (1)', 'transfer_mark']);
  expect(await seriousViolations(page)).toEqual([]);

  if (pgParts && pgMeasurements) {
    // The same applications on PostgreSQL: parts by its Alembic migrations, measurements' upstream table.
    referenceApp('parts', PARTS_SETUP, [pgParts]);
    referenceApp('measurements', MEASUREMENTS_PG_SETUP, [pgMeasurements]);
    for (const [name, url] of [
      ['ra-parts-pg', pgParts],
      ['ra-measurements-pg', pgMeasurements],
    ] as const) {
      const target = new URL(url);
      await addProfile(page, {
        name,
        engine: 'postgresql',
        environment: 'dev',
        host: target.hostname,
        port: Number(target.port || 5432),
        database: target.pathname.slice(1),
        user: process.env.TABRION_STUDIO_E2E_PG_USER ?? decodeURIComponent(target.username),
        sslmode: 'disable',
        // A server with password authentication (STU-050 ran it on scram-sha-256): the server reads the password
        // from this variable, which it inherits; without it the profile has no password, for a trust-auth server.
        ...(process.env.TABRION_STUDIO_E2E_PG_PASSWORD
          ? { password: 'env:TABRION_STUDIO_E2E_PG_PASSWORD' }
          : {}),
      });
    }
    await page.reload();
    await expect(page.getByTestId('backend-status')).toContainText('Local server');
    await openProfile(page, 'ra-parts-pg');
    await expect(tree.getByRole('treeitem', { name: /^public \(\d+\)$/ })).toBeVisible();
    await tree.getByRole('treeitem', { name: 'part', exact: true }).click();
    expect(await columns(page)).toContain('material_id');
    await page.getByRole('tab', { name: 'Constraints' }).click();
    await expect(page.getByTestId('detail-fk')).toHaveText(
      'fk_part_material: material_id → public.material (id)',
    );
    await page.getByRole('tab', { name: 'Relationships' }).click();
    await expect(page.getByTestId('detail-referenced-by')).toHaveCount(3);
    await tree.getByRole('treeitem', { name: 'Extensions' }).click();
    await expect(page.getByTestId('detail-extension').first()).toBeVisible();
    expect(await seriousViolations(page)).toEqual([]);
    await openProfile(page, 'ra-measurements-pg');
    await tree.getByRole('treeitem', { name: 'measurement', exact: true }).click();
    expect(await columns(page)).toEqual([
      'device_id',
      'channel',
      'ts_utc',
      'value',
      'unit',
      'quality',
      'source_seq',
    ]);
    await page.getByRole('tab', { name: 'Constraints' }).click();
    await expect(page.getByTestId('detail-pk')).toContainText('device_id, channel, ts_utc');
  }
  await expectNoViolations(page, violations);
});
