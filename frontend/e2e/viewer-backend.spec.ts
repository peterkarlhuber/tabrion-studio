/**
 * STU-013 page end to end: the explorer's "Data" tab in a real `tabrion-studio` process with a SQLite database, under
 * the strict Content-Security-Policy of STU-003. First page, Next and Previous through the server's keyset tokens,
 * sort, a filter the server binds, the cell detail with "Load in full" (70 000 characters, more than a page cell
 * holds), a table without a primary key, D-02 before a PROD profile's data, a connection closed elsewhere, and wide
 * rows at the largest result size (`max_result_mb = 200`) within the browser's memory: the page asks for no more
 * rows than fit in 64 MB at the column types' widest values (`views/viewer/pageRows.ts`).
 *
 * Runs only when TABRION_STUDIO_E2E_PYTHON names a Python in which Studio is installed with its built frontend
 * (`python scripts/build_frontend.py` and `pip install -e .`, or the wheel), like e2e/backend.spec.ts.
 */
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { test, expect, type Page } from '@playwright/test';

import {
  expectNoViolations,
  foreignRequests,
  recordViolations,
  seriousViolations,
} from './support';

const python = process.env.TABRION_STUDIO_E2E_PYTHON;
test.skip(!python, 'set TABRION_STUDIO_E2E_PYTHON to run against a real tabrion-studio');
test.describe.configure({ mode: 'serial' });

/** I-1 of stu-008-review.md: the real server never reaches the developer's keyring. */
const NO_KEYRING_ENV = { ...process.env, PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring' };
const LINK = /do not share\): (http:\/\/127\.0\.0\.1:\d+\/launch#\S+)/;

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

test.beforeAll(async () => {
  root = mkdtempSync(join(tmpdir(), 'studio-viewer-e2e-'));
  mkdirSync(join(root, 'project'));
  database = join(root, 'parts.db');
  // parts: 250 rows with a primary key; note of part 7 is 70 000 characters (more than a page cell holds);
  // log: no primary key (rowid paging); wide: a view of 200 rows of 12 texts of 30 000 "€" each, all different.
  const wide = Array.from(
    { length: 12 },
    (_, column) => `n || '${column}' || replace(hex(zeroblob(30000)), '00', '€') AS c${column}`,
  ).join(', ');
  execFileSync(python as string, [
    '-I',
    '-c',
    [
      'import sqlite3, sys',
      'db = sqlite3.connect(sys.argv[1])',
      "db.execute('create table parts (id integer primary key, material text, weight real, note text)')",
      "db.executemany('insert into parts values (?, ?, ?, ?)', [(i, 'Aluminium' if i % 2 else 'Steel', i * 0.5, ('long ' + 'x' * 70000) if i == 7 else (None if i % 3 == 0 else 'n' + str(i))) for i in range(1, 251)])",
      "db.execute('create table log (body text)')",
      "db.executemany('insert into log values (?)', [('<img src=x onerror=alert(1)>',), ('two\\nlines',), (None,)])",
      'db.execute(sys.argv[2])',
      'db.commit()',
    ].join('\n'),
    database,
    `create view wide as with recursive c(n) as (select 1 union all select n + 1 from c where n < 200) select n as id, ${wide} from c`,
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

/** Sign in, record CSP violations and page errors. */
async function signIn(page: Page): Promise<string[]> {
  const violations = await recordViolations(page);
  await page.goto(links.shift() ?? newLink());
  await expect(page.getByTestId('backend-status')).toContainText('Local server');
  return violations;
}

/** A call to the API from the page, with its CSRF header. */
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

/** A call to the API from the page that returns the JSON body. */
async function apiJson(page: Page, path: string): Promise<unknown> {
  return page.evaluate(async (path) => {
    const csrf = sessionStorage.getItem('tabrion-studio.csrf') ?? '';
    const response = await fetch(path, { headers: { 'X-Tabrion-CSRF': csrf } });
    return response.json();
  }, path);
}

/** The digest of the user's profiles file, which a save must name. */
async function userDigest(page: Page): Promise<string | null> {
  const doc = (await apiJson(page, '/api/profiles')) as { user_file?: { digest?: string | null } };
  return doc.user_file?.digest ?? null;
}

/** Open the explorer on a profile (D-02 is answered by the caller for PROD). */
async function openExplorer(page: Page, profile: string): Promise<void> {
  await page.getByRole('link', { name: 'Explorer' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Explorer' })).toBeVisible();
  await page.getByRole('combobox').first().selectOption(`user:${profile}`);
  await page.getByTestId('explorer-open').click();
}

async function showData(page: Page, name: string): Promise<void> {
  await page.getByRole('treeitem', { name, exact: true }).click();
  await expect(page.getByTestId('explorer-show-data')).toBeEnabled();
  await page.getByTestId('explorer-show-data').click();
}

const status = (page: Page) => page.getByTestId('viewer-status');
const firstCell = (page: Page) => page.getByRole('grid').getByRole('gridcell').first();

test('setup: a DEV and a PROD profile, and the largest result size', async ({ page }) => {
  await signIn(page);
  for (const [name, environment] of [
    ['e2e-viewer', 'dev'],
    ['e2e-viewer-prod', 'prod'],
  ]) {
    expect(
      await api(page, 'POST', '/api/profiles', {
        profile: { name, engine: 'sqlite', environment, path: database },
        digest: await userDigest(page),
        overwrite: false,
      }),
    ).toBe(201);
  }
  expect(
    await api(page, 'PUT', '/api/settings', {
      settings: {
        log_sql: false,
        limits: { max_rows: 10_000, statement_timeout_ms: 30_000, max_result_mb: 200 },
      },
      digest: null,
      overwrite: false,
    }),
  ).toBe(200);
});

test('pages, sort, a filter and Load in full under the CSP; nothing but reads', async ({
  page,
}) => {
  const foreign = foreignRequests(page, () => base);
  const violations = await signIn(page);
  const writes: string[] = [];
  page.on('request', (request) => {
    if (request.method() !== 'GET')
      writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
  });
  await openExplorer(page, 'e2e-viewer');
  await showData(page, 'parts');
  await expect(status(page)).toHaveText('Rows 1–100; more rows follow.');
  await expect(firstCell(page)).toHaveText('1');
  expect(await seriousViolations(page)).toEqual([]);
  await page.getByTestId('viewer-next').click();
  await expect(status(page)).toHaveText('Rows 101–200; more rows follow.');
  await expect(firstCell(page)).toHaveText('101');
  await page.getByTestId('viewer-next').click();
  await expect(status(page)).toHaveText('Rows 201–250; this is the last page.');
  await page.getByTestId('viewer-previous').click();
  await expect(firstCell(page)).toHaveText('101');
  // Sort by weight, descending: the first page starts again.
  await page.getByTestId('viewer-sort').selectOption('weight');
  await page.getByTestId('viewer-direction').selectOption('desc');
  await expect(status(page)).toHaveText('Rows 1–100; more rows follow.');
  await expect(firstCell(page)).toHaveText('250');
  // A filter the server binds: material = Steel (the even ids), weight descending.
  await page.getByTestId('viewer-add-filter').click();
  await page.getByLabel('Column of filter 1').selectOption('material');
  await page.getByLabel('Value of filter 1').fill('Steel');
  await page.getByTestId('viewer-apply').click();
  await expect(page.getByTestId('viewer-filters-applied')).toHaveText('(1 applied)');
  await expect(status(page)).toHaveText('Rows 1–100; more rows follow.');
  await expect(firstCell(page)).toHaveText('250');
  await page.getByTestId('viewer-next').click();
  await expect(status(page)).toHaveText('Rows 101–125; this is the last page.');
  // A value with "%" is a literal: nothing contains "St%".
  await page.getByLabel('Operator of filter 1').selectOption('contains');
  await page.getByLabel('Value of filter 1').fill('St%');
  await page.getByTestId('viewer-apply').click();
  await expect(status(page)).toHaveText('No row matches the filters.');
  await page.getByTestId('viewer-clear').click();
  await page.getByTestId('viewer-sort').selectOption('');
  await expect(status(page)).toHaveText('Rows 1–100; more rows follow.');
  // Part 7's note is cut on the page; the detail loads it whole from the server.
  await page.locator('[data-row="6"][data-column="3"]').click();
  await expect(page.getByTestId('cell-cut')).toBeVisible();
  await page.getByTestId('viewer-load-full').click();
  await expect(page.getByTestId('viewer-full-done')).toHaveText(
    'The whole value is shown (70,005 bytes).',
  );
  expect((await page.getByTestId('cell-value').textContent())?.length).toBe(70_005);
  // A table without a primary key is paged by rowid and says so; values stay text.
  await showData(page, 'log');
  await expect(status(page)).toHaveText('Rows 1–3; this is the last page.');
  await expect(page.getByTestId('viewer-hint')).toContainText('No primary key');
  await expect(firstCell(page)).toHaveText('<img src=x onerror=alert(1)>');
  await expect(page.getByTestId('explorer-workspace').locator('img')).toHaveCount(0);
  expect(writes).toEqual(['POST /api/connections']);
  await expectNoViolations(page, violations);
  expect(foreign).toEqual([]);
});

test('a PROD profile shows data only after D-02', async ({ page }) => {
  const violations = await signIn(page);
  await openExplorer(page, 'e2e-viewer-prod');
  const dialog = page.getByRole('dialog', { name: 'Production database' });
  await expect(dialog).toBeVisible();
  const open = (await apiJson(page, '/api/connections')) as {
    connections: { profile_id: string }[];
  };
  expect(open.connections.filter((c) => c.profile_id === 'user:e2e-viewer-prod')).toEqual([]);
  await dialog.getByRole('button', { name: 'Connect' }).click();
  await showData(page, 'parts');
  await expect(status(page)).toHaveText('Rows 1–100; more rows follow.');
  await expect(page.getByTestId('explorer-workspace').getByTestId('env-badge')).toHaveAttribute(
    'data-env',
    'prod',
  );
  await expectNoViolations(page, violations);
});

test('a connection closed elsewhere: the explorer drops it', async ({ page }) => {
  const violations = await signIn(page);
  await openExplorer(page, 'e2e-viewer');
  await showData(page, 'parts');
  await expect(status(page)).toHaveText('Rows 1–100; more rows follow.');
  const open = (await apiJson(page, '/api/connections')) as {
    connections: { connection_id: string; profile_id: string }[];
  };
  for (const item of open.connections.filter((c) => c.profile_id === 'user:e2e-viewer')) {
    expect(
      await api(page, 'DELETE', '/api/connections', { connection_id: item.connection_id }),
    ).toBe(204);
  }
  // The server dropped the closed connection's pages: Next says so and offers the first page, which finds the
  // connection gone; the explorer drops it and the focus goes to "Open".
  await page.getByTestId('viewer-next').click();
  await expect(page.getByTestId('viewer-problem')).toContainText(
    'Studio no longer keeps this page.',
  );
  await page.getByTestId('viewer-first-page').click();
  await expect(page.getByTestId('explorer-workspace')).toHaveCount(0);
  await expect(page.getByTestId('explorer-problem')).toBeVisible();
  await expect(page.getByTestId('explorer-open')).toBeFocused();
  await expectNoViolations(page, violations);
});

test('wide rows at max_result_mb = 200 stay within the memory limit', async ({
  page,
  browserName,
}) => {
  test.skip(browserName !== 'chromium', 'chromium only: the heap is measured through CDP');
  test.setTimeout(180_000);
  const violations = await signIn(page);
  const limits: number[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname.endsWith('/rows')) limits.push(Number(url.searchParams.get('limit')));
  });
  await openExplorer(page, 'e2e-viewer');
  await page.getByRole('treeitem', { name: 'Views (1)' }).click();
  await showData(page, 'wide');
  await expect(status(page)).toHaveText(/^Rows 1–/, { timeout: 60_000 });
  await page.getByTestId('viewer-page-size').selectOption('1000');
  await expect(page.getByTestId('viewer-limited')).toBeVisible();
  await expect(status(page)).toHaveText(/^Rows 1–19; more rows follow\.$/, { timeout: 60_000 });
  for (let i = 0; i < 3; i += 1) {
    await page.getByTestId('viewer-next').click();
    await expect(status(page)).toHaveText(new RegExp(`^Rows ${19 * (i + 1) + 1}–`), {
      timeout: 60_000,
    });
  }
  // 13 columns of a view with no declared type: each value can be 64 K characters of 4 bytes (above U+FFFF), so
  // 64 MB hold 19 rows.
  expect(limits.at(-1)).toBe(19);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('HeapProfiler.collectGarbage');
  await cdp.send('Performance.enable');
  const { metrics } = await cdp.send('Performance.getMetrics');
  const used = metrics.find((m) => m.name === 'JSHeapUsedSize')?.value ?? Infinity;
  const total = metrics.find((m) => m.name === 'JSHeapTotalSize')?.value ?? Infinity;
  console.warn(
    `real server, viewer wide rows: JS heap used ${(used / 2 ** 20).toFixed(1)} MB, total ${(total / 2 ** 20).toFixed(1)} MB`,
  );
  expect(total).toBeLessThan(300 * 2 ** 20);
  // A page of 1000 asked rows would be cut by the server near 200 MB of JSON (about 185 rows, over 130 MB here).
  expect(used).toBeLessThan(90 * 2 ** 20);
  await expectNoViolations(page, violations);
});
