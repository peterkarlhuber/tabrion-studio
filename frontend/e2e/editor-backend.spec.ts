/**
 * STU-012 end to end: the built SQL editor in a real `tabrion-studio` process with a SQLite profile, under the strict
 * Content-Security-Policy of STU-003 (CodeMirror's styles live in a shadow root, see src/views/editor/SqlEditor.tsx):
 * run, the grid, parameters, a refused write, cancel of an endless query within a second (STU-T-020 from the page),
 * 100 000 rows within the memory limit of 03 section 10 (300 MB of browser memory), and wide rows at the largest
 * result the server allows (`max_result_mb = 200`) within it too (M-4 of stu-012-review.md). Durations must not read
 * as 0: the server sends them with one decimal (C-3).
 *
 * Runs only when TABRION_STUDIO_E2E_PYTHON names a Python in which Studio is installed with its built frontend
 * (`python scripts/build_frontend.py` and `pip install -e .`, or the wheel), like e2e/backend.spec.ts.
 */
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { test, expect, type Page } from '@playwright/test';

import { foreignRequests, seriousViolations } from './support';

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
  root = mkdtempSync(join(tmpdir(), 'studio-editor-e2e-'));
  mkdirSync(join(root, 'project'));
  database = join(root, 'parts.db');
  execFileSync(python as string, [
    '-I',
    '-c',
    [
      'import sqlite3, sys',
      'db = sqlite3.connect(sys.argv[1])',
      "db.execute('create table parts (id integer primary key, material text, weight real, note text)')",
      "db.executemany('insert into parts values (?, ?, ?, ?)', [(i, 'Aluminium' if i % 2 else 'Steel', i * 0.5, None if i % 3 == 0 else 'n' + str(i)) for i in range(1, 51)])",
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
  const violations: string[] = [];
  page.on('console', (message) => {
    if (/Content Security Policy|Refused to/i.test(message.text())) violations.push(message.text());
  });
  page.on('pageerror', (error) => violations.push(String(error)));
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

async function openEditor(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'SQL editor' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'SQL editor' })).toBeVisible();
  await expect(page.locator('.cm-content')).toBeVisible();
  await expect(page.getByTestId('editor-profile')).toHaveValue(/e2e-parts/);
}

async function type(page: Page, sql: string): Promise<void> {
  await page.locator('.cm-content').click();
  await page.keyboard.press('Control+A');
  await page.keyboard.press('Delete');
  // insertText: CodeMirror's autocompletion and bracket closing would change typed text.
  await page.keyboard.insertText(sql);
}

/** A duration in the status line that is not zero ("0 ms" was every duration before C-3 of stu-012-review.md). */
const MS = String.raw`(?!0 ms)\d[\d,.]* ms`;

test('setup: a SQLite profile, a row limit of 100 000 and the largest result size', async ({
  page,
}) => {
  await signIn(page);
  expect(
    await api(page, 'POST', '/api/profiles', {
      profile: { name: 'e2e-parts', engine: 'sqlite', environment: 'dev', path: database },
      digest: null,
      overwrite: false,
    }),
  ).toBe(201);
  expect(
    await api(page, 'PUT', '/api/settings', {
      settings: {
        log_sql: false,
        limits: { max_rows: 100_000, statement_timeout_ms: 30_000, max_result_mb: 200 },
      },
      digest: null,
      overwrite: false,
    }),
  ).toBe(200);
});

test('runs a query under the CSP, with the grid, parameters and the detail panel', async ({
  page,
}) => {
  const foreign = foreignRequests(page, () => base);
  const violations = await signIn(page);
  await openEditor(page);
  await type(
    page,
    'select id, material, weight, note from parts where material = :material order by id',
  );
  await page.getByTestId('editor-run').click();
  await expect(
    page.getByTestId('param-panel').getByRole('textbox', { name: ':material' }),
  ).toBeFocused();
  await page.keyboard.type('Aluminium');
  await page.locator('.cm-content').click();
  await page.keyboard.press('Control+Enter');
  await expect(page.getByTestId('result-status')).toHaveText(new RegExp(`^25 rows · ${MS}$`));
  const grid = page.getByRole('grid', { name: 'Result 1' });
  await expect(grid).toHaveAttribute('aria-rowcount', '26');
  await expect(grid.getByRole('columnheader')).toHaveText(['id', 'material', 'weight', 'note']);
  await expect(grid.getByRole('row').nth(1).getByRole('gridcell')).toHaveText([
    '1',
    'Aluminium',
    '0.5',
    'n1',
  ]);
  await expect(grid.getByRole('row').nth(2).getByRole('gridcell').nth(3)).toHaveText('NULL');
  await grid.focus();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('cell-value')).toHaveText('3');
  for (const theme of ['Light', 'Dark']) {
    await page.getByTestId('open-palette').click();
    await page.keyboard.type(`theme ${theme}`);
    await page.keyboard.press('Enter');
    expect(await seriousViolations(page)).toEqual([]);
  }
  // No CSP violation: CodeMirror's styles are a constructed style sheet in its shadow root.
  expect(violations).toEqual([]);
  expect(foreign).toEqual([]);
});

test('a write is refused by the server; nothing in the page writes', async ({ page }) => {
  const violations = await signIn(page);
  await openEditor(page);
  await type(page, "update parts set material = 'Gold'");
  await page.getByTestId('editor-run').click();
  await expect(page.getByTestId('result-status')).toHaveText(
    /^Studio runs only reading statements/,
  );
  await type(page, "select count(*) from parts where material = 'Gold'");
  await page.getByTestId('editor-run').click();
  await expect(page.getByRole('grid').getByRole('gridcell').first()).toHaveText('0');
  expect(violations).toEqual([]);
});

test('cancel stops an endless query within a second (STU-T-020)', async ({ page }) => {
  const violations = await signIn(page);
  await openEditor(page);
  await type(
    page,
    'with recursive c(n) as (select 1 union all select n + 1 from c) select count(*) from c',
  );
  await page.locator('.cm-content').press('Control+Enter');
  await expect(page.getByRole('progressbar', { name: 'Query running' })).toBeVisible();
  await page.waitForTimeout(500);
  const started = Date.now();
  await page.keyboard.press('Control+.');
  const status = page.getByTestId('result-status');
  await expect(status).toHaveText(/^Canceled after \d+\.\d s\.$/, { timeout: 5_000 });
  expect(Date.now() - started).toBeLessThan(1_500);
  // The query ran for at least the 500 ms waited above.
  const seconds = Number(/after (\d+\.\d) s/.exec((await status.textContent()) ?? '')?.[1]);
  expect(seconds).toBeGreaterThanOrEqual(0.4);
  // The connection works afterwards.
  await type(page, 'select 42');
  await page.getByTestId('editor-run').click();
  await expect(page.getByRole('grid').getByRole('gridcell').first()).toHaveText('42');
  expect(violations).toEqual([]);
});

test('100 000 rows stay within the memory limit (03 section 10: 300 MB)', async ({ page }) => {
  test.setTimeout(180_000);
  const violations = await signIn(page);
  await openEditor(page);
  await type(
    page,
    "with recursive c(n) as (select 1 union all select n + 1 from c where n < 100000) select n, 'row ' || n as label, n * 1.5 as value from c",
  );
  await page.getByTestId('editor-run').click();
  await expect(page.getByTestId('result-status')).toHaveText(/^1,000 rows, capped at 1,000/);
  await page.getByRole('button', { name: 'Load up to 100,000 rows' }).click();
  await expect(page.getByTestId('result-status')).toHaveText(new RegExp(`^100,000 rows · ${MS}$`), {
    timeout: 60_000,
  });
  const grid = page.getByRole('grid');
  await expect(grid).toHaveAttribute('aria-rowcount', '100001');
  for (let step = 0; step <= 20; step += 1) {
    await grid.evaluate((el, fraction) => {
      el.scrollTop = (el.scrollHeight - el.clientHeight) * fraction;
    }, step / 20);
    await expect(grid.getByRole('row').nth(1).getByRole('gridcell').first()).not.toHaveText('…');
  }
  await grid.focus();
  await page.keyboard.press('Control+End');
  await expect(grid.locator('[aria-rowindex="100001"]').getByRole('gridcell').first()).toHaveText(
    '100000',
  );
  expect(await grid.getByRole('row').count()).toBeLessThan(120);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('HeapProfiler.collectGarbage');
  await cdp.send('Performance.enable');
  const { metrics } = await cdp.send('Performance.getMetrics');
  const used = metrics.find((m) => m.name === 'JSHeapUsedSize')?.value ?? Infinity;
  const total = metrics.find((m) => m.name === 'JSHeapTotalSize')?.value ?? Infinity;
  console.warn(
    `real server, 100 000 rows: JS heap used ${(used / 2 ** 20).toFixed(1)} MB, total ${(total / 2 ** 20).toFixed(1)} MB`,
  );
  expect(total).toBeLessThan(300 * 2 ** 20);
  expect(used).toBeLessThan(100 * 2 ** 20);
  expect(violations).toEqual([]);
});

test('wide rows at max_result_mb = 200 stay within the memory limit (M-4)', async ({ page }) => {
  test.setTimeout(240_000);
  const violations = await signIn(page);
  await openEditor(page);
  // Three texts of 30 000 characters "€" per row: about 270 KB of UTF-8 and 180 KB in V8 per row. Each text starts
  // with its row and column, so no two are equal (equal strings could be shared by the JSON parser).
  const wide = (column: string) => `n || '${column}' || replace(hex(zeroblob(30000)), '00', '€')`;
  await type(
    page,
    `with recursive c(n) as (select 1 union all select n + 1 from c where n < 1200) select n, ${wide('a')} as a, ${wide('b')} as b, ${wide('c')} as c from c`,
  );
  await page.getByTestId('editor-run').click();
  await expect(page.getByTestId('result-status')).toHaveText(/rows/, { timeout: 120_000 });
  const grid = page.getByRole('grid');
  const count = Number(await grid.getAttribute('aria-rowcount')) - 1;
  console.warn(`real server, wide rows: ${count} rows in the result`);
  expect(count).toBeGreaterThan(500);
  for (let step = 0; step <= 40; step += 1) {
    await grid.evaluate((el, fraction) => {
      el.scrollTop = (el.scrollHeight - el.clientHeight) * fraction;
    }, step / 40);
    await expect(grid.getByRole('row').nth(1).getByRole('gridcell').first()).not.toHaveText('…', {
      timeout: 20_000,
    });
  }
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('HeapProfiler.collectGarbage');
  await cdp.send('Performance.enable');
  const { metrics } = await cdp.send('Performance.getMetrics');
  const used = metrics.find((m) => m.name === 'JSHeapUsedSize')?.value ?? Infinity;
  const total = metrics.find((m) => m.name === 'JSHeapTotalSize')?.value ?? Infinity;
  console.warn(
    `real server, wide rows: JS heap used ${(used / 2 ** 20).toFixed(1)} MB, total ${(total / 2 ** 20).toFixed(1)} MB`,
  );
  expect(total).toBeLessThan(300 * 2 ** 20);
  // The byte budget is 64 MB; all rows held would be well above 100 MB.
  expect(used).toBeLessThan(140 * 2 ** 20);
  expect(violations).toEqual([]);
});

/** A call to the API from the page that returns the JSON body. */
async function apiJson(page: Page, path: string): Promise<unknown> {
  return page.evaluate(async (path) => {
    const csrf = sessionStorage.getItem('tabrion-studio.csrf') ?? '';
    const response = await fetch(path, { headers: { 'X-Tabrion-CSRF': csrf } });
    return response.json();
  }, path);
}

async function openProfiles(page: Page, name: string): Promise<string[]> {
  const doc = (await apiJson(page, '/api/connections')) as {
    connections: { profile_id?: string; environment?: string }[];
  };
  return doc.connections
    .filter((item) => item.profile_id === `user:${name}`)
    .map((item) => item.environment ?? '');
}

/** The digest of the user's profiles file, which a save must name. */
async function userDigest(page: Page): Promise<string | null> {
  const doc = (await apiJson(page, '/api/profiles')) as { user_file?: { digest?: string | null } };
  return doc.user_file?.digest ?? null;
}

/** Change a profile's environment as another page would. */
async function retag(page: Page, name: string, environment: string): Promise<void> {
  expect(
    await api(page, 'PUT', '/api/profiles', {
      id: `user:${name}`,
      profile: { name, engine: 'sqlite', environment, path: database },
      digest: await userDigest(page),
      overwrite: false,
    }),
  ).toBe(200);
}

test('a profile changed to PROD elsewhere never connects without D-02 (C-5)', async ({ page }) => {
  const violations = await signIn(page);
  for (const name of ['e2e-dev3', 'e2e-dev4']) {
    expect(
      await api(page, 'POST', '/api/profiles', {
        profile: { name, engine: 'sqlite', environment: 'dev', path: database },
        digest: await userDigest(page),
        overwrite: false,
      }),
    ).toBe(201);
  }
  await openEditor(page);
  const notice = page.getByTestId('editor-notice');

  // First connection: the page read e2e-dev3 as DEV; another page makes it PROD before Run.
  await page.getByTestId('editor-profile').selectOption('user:e2e-dev3');
  await type(page, 'select 1');
  await retag(page, 'e2e-dev3', 'prod');
  await page.getByTestId('editor-run').click();
  await expect(notice).toHaveText(
    /^The profile e2e-dev3 changed since this page read it \(now PROD\)/,
  );
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(await openProfiles(page, 'e2e-dev3')).toEqual([]);
  // The page shows it as PROD now, and Run asks D-02.
  await page.getByTestId('editor-run').click();
  const dialog = page.getByRole('dialog', { name: 'Production database' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  expect(await openProfiles(page, 'e2e-dev3')).toEqual([]);

  // A connection that went stale: e2e-dev4 ran on DEV, then turns PROD.
  await page.getByTestId('editor-profile').selectOption('user:e2e-dev4');
  await type(page, 'select 1');
  await page.getByTestId('editor-run').click();
  await expect(page.getByRole('grid').getByRole('gridcell').first()).toHaveText('1');
  expect(await openProfiles(page, 'e2e-dev4')).toEqual(['dev']);
  await retag(page, 'e2e-dev4', 'prod');
  await page.getByTestId('editor-run').click();
  await expect(notice).toHaveText(
    /^The profile e2e-dev4 changed since this page read it \(now PROD\)/,
  );
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('grid')).toHaveCount(0);
  expect(await openProfiles(page, 'e2e-dev4')).not.toContain('prod');
  await page.getByTestId('editor-run').click();
  await expect(page.getByRole('dialog', { name: 'Production database' })).toBeVisible();
  expect(violations).toEqual([]);
});

test('a profile list that cannot be read again opens no connection (L-9)', async ({ page }) => {
  const violations = await signIn(page);
  expect(
    await api(page, 'POST', '/api/profiles', {
      profile: { name: 'e2e-dev5', engine: 'sqlite', environment: 'dev', path: database },
      digest: await userDigest(page),
      overwrite: false,
    }),
  ).toBe(201);
  await openEditor(page);
  await page.getByTestId('editor-profile').selectOption('user:e2e-dev5');
  await type(page, 'select 1');
  // From now on the server's profile list fails, so the editor cannot check the profile before connecting.
  await page.route('**/api/profiles', (route) => route.fulfill({ status: 500, body: '{}' }));
  const opens: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/connections') {
      opens.push(request.url());
    }
  });
  await page.getByTestId('editor-run').click();
  await expect(page.getByTestId('editor-connect-problem')).toHaveText(
    'The local server does not answer.',
  );
  await expect(page.getByRole('grid')).toHaveCount(0);
  expect(opens).toEqual([]);
  await page.unroute('**/api/profiles');
  expect(await openProfiles(page, 'e2e-dev5')).toEqual([]);
  expect(violations).toEqual([]);
});
