/**
 * QA-S1 S1-32 end to end, owner decision of 2026-11-14 14:13Z (option A): "Disconnect" in the connection manager is
 * respected. "Run" in the SQL editor afterwards opens no connection and sends no query (checked on the network and on
 * the server); the banner "Disconnected" with "Reconnect" shows, and the editor says that nothing ran. Only
 * "Reconnect" opens the connection again and then runs the query the user asked for. On PROD, "Reconnect" asks D-02
 * again, and "Cancel" there leaves the profile disconnected. The header, the banner, the editor and the connection
 * manager show the same state before, during and after. A connection the server closed (a loss, TD-034) is opened
 * again by itself on DEV, as before (e2e/connection-lost-backend.spec.ts covers its banner).
 *
 * Runs only when TABRION_STUDIO_E2E_PYTHON names a Python in which Studio is installed with its built frontend, like
 * e2e/connection-lost-backend.spec.ts.
 */
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { test, expect, type Page } from '@playwright/test';

import { expectNoViolations, recordViolations } from './support';

const python = process.env.TABRION_STUDIO_E2E_PYTHON;
test.skip(!python, 'set TABRION_STUDIO_E2E_PYTHON to run against a real tabrion-studio');
test.describe.configure({ mode: 'serial' });

/** I-1 of stu-008-review.md: the real server never reaches the developer's keyring. */
const STUDIO_ENV = { ...process.env, PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring' };
const LINK = /do not share\): (http:\/\/127(?:\.\d{1,3}){3}:\d+\/launch#\S+)/;

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
  root = mkdtempSync(join(tmpdir(), 'studio-disconnect-e2e-'));
  mkdirSync(join(root, 'project'));
  database = join(root, 'shop.db');
  execFileSync(python as string, [
    '-I',
    '-c',
    [
      'import sqlite3, sys',
      'db = sqlite3.connect(sys.argv[1])',
      "db.execute('create table orders (id integer primary key, status text)')",
      "db.executemany('insert into orders (status) values (?)', [('paid',), ('open',), ('paid',)])",
      'db.commit()',
    ].join('\n'),
    database,
  ]);
  studio = spawn(python as string, args(), {
    stdio: ['ignore', 'pipe', 'inherit'],
    env: STUDIO_ENV,
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
  const out = execFileSync(python as string, args(), { encoding: 'utf-8', env: STUDIO_ENV });
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

/** A call to the API from the page, with its CSRF header; the status and the JSON body (or null). */
async function api(
  page: Page,
  method: string,
  path: string,
  body?: unknown,
): Promise<{ status: number; doc: unknown }> {
  return page.evaluate(
    async ({ method, path, body }) => {
      const csrf = sessionStorage.getItem('tabrion-studio.csrf') ?? '';
      const response = await fetch(path, {
        method,
        headers: { 'X-Tabrion-CSRF': csrf, 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const text = await response.text();
      return { status: response.status, doc: text === '' ? null : (JSON.parse(text) as unknown) };
    },
    { method, path, body },
  );
}

async function addProfile(page: Page, name: string, environment: 'dev' | 'prod'): Promise<void> {
  const list = await api(page, 'GET', '/api/profiles');
  const digest = (list.doc as { user_file?: { digest?: string | null } }).user_file?.digest ?? null;
  const profile = { name, engine: 'sqlite', environment, path: database };
  expect(
    (await api(page, 'POST', '/api/profiles', { profile, digest, overwrite: false })).status,
  ).toBe(201);
}

/** The server's open connections of `profileId`. */
async function openOnServer(page: Page, profileId: string): Promise<string[]> {
  const open = await api(page, 'GET', '/api/connections');
  return (open.doc as { connections: { connection_id: string; profile_id: string }[] }).connections
    .filter((item) => item.profile_id === profileId)
    .map((item) => item.connection_id);
}

/** How many statements the server's query history holds. */
async function historyCount(page: Page): Promise<number> {
  const history = await api(page, 'GET', '/api/history');
  return (history.doc as { entries: unknown[] }).entries.length;
}

/** The requests that open a connection or start a query, as "METHOD path", from now on. */
function watchWrites(page: Page): string[] {
  const seen: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && (path === '/api/connections' || path === '/api/queries')) {
      seen.push(`POST ${path}`);
    }
  });
  return seen;
}

const SQL = 'select count(*) as n from orders';
const header = (page: Page) => page.getByRole('banner').getByRole('group', { name: 'Status' });
const banner = (page: Page) => page.getByTestId('connection-lost');
const production = (page: Page) => page.getByRole('dialog', { name: 'Production database' });

async function openEditor(page: Page, profileId: string): Promise<void> {
  // Each test starts with one empty tab (the drafts live in Studio's state database, TD-033).
  expect((await api(page, 'PUT', '/api/drafts', { tabs: [], active_tab: null })).status).toBe(200);
  await page.getByRole('link', { name: 'SQL editor' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'SQL editor' })).toBeVisible();
  await expect(page.locator('.cm-content')).toBeVisible();
  await page.getByTestId('editor-profile').selectOption(profileId);
  await page.locator('.cm-content').click();
  await page.keyboard.press('Control+A');
  await page.keyboard.press('Delete');
  await page.keyboard.insertText(SQL);
}

async function backToEditor(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'SQL editor' }).click();
  await expect(page.locator('.cm-content')).toHaveText(SQL);
}

/** "Connections" → the profile → its state in the connection manager. */
async function managerState(page: Page, name: string) {
  await page.getByRole('link', { name: 'Connections' }).click();
  await page.getByTestId('connection-item').filter({ hasText: name }).first().click();
  return page.getByTestId('connection-detail');
}

/** Run, then "Disconnect" in the connection manager of the same tab; checks the state before and after. */
async function runThenDisconnect(page: Page, name: string, prod: boolean): Promise<void> {
  const profileId = `user:${name}`;
  await openEditor(page, profileId);
  await page.getByTestId('editor-run').click();
  if (prod) await production(page).getByRole('button', { name: 'Connect' }).click();
  await expect(page.getByTestId('result-status')).toHaveText(/^1 row/);
  // Before: the header, the connection manager and the server agree that the profile is connected.
  await expect(header(page)).toContainText(`${name}, connected`);
  const detail = await managerState(page, name);
  await expect(detail.getByTestId('connection-state')).toHaveText('open (read-only)');
  expect(await openOnServer(page, profileId)).toHaveLength(1);

  await detail.getByRole('button', { name: 'Disconnect', exact: true }).click();
  await expect(detail.getByRole('button', { name: 'Connect', exact: true })).toBeVisible();
  await expect(detail.getByTestId('connection-state')).toHaveText('not open');
  await expect(header(page)).toContainText('No connection');
  // "Disconnect" itself shows no banner: it asks only when something needs the connection.
  await expect(banner(page)).toHaveCount(0);
  expect(await openOnServer(page, profileId)).toEqual([]);
}

/** "Run" after the disconnect: the banner and the editor's notice; nothing opened or sent. */
async function runAndExpectNotice(page: Page, name: string, writes: string[]): Promise<void> {
  const profileId = `user:${name}`;
  const ran = await historyCount(page);
  await backToEditor(page);
  await page.getByTestId('editor-run').click();
  await expect(banner(page)).toHaveAttribute('data-cause', 'disconnected');
  await expect(banner(page)).toContainText(`Disconnected: ${name}`);
  await expect(banner(page)).toContainText('You disconnected this connection.');
  await expect(page.getByTestId('editor-notice')).toContainText(
    `You disconnected ${name}. Nothing was run.`,
  );
  await expect(header(page)).toContainText('No connection');
  await expect(production(page)).toHaveCount(0);
  // A second "Run" asks again, with the one banner.
  await page.getByTestId('editor-run').click();
  await expect(banner(page)).toHaveCount(1);
  // Nothing reached the server: no open, no query (the requests, the open connections and the query history).
  expect(writes).toEqual([]);
  expect(await openOnServer(page, profileId)).toEqual([]);
  expect(await historyCount(page)).toBe(ran);
  expect(writes).toEqual([]);
}

/** After "Reconnect": the query ran, and every view says connected. */
async function expectRanAndConnected(page: Page, name: string): Promise<void> {
  await expect(banner(page)).toHaveCount(0);
  await expect(page.getByTestId('result-status')).toHaveText(/^1 row/);
  await expect(page.getByTestId('editor-notice')).toHaveText('');
  await expect(header(page)).toContainText(`${name}, connected`);
  await expect(page.locator('.cm-content')).toHaveText(SQL);
  const opened = await openOnServer(page, `user:${name}`);
  expect(opened).toHaveLength(1);
  const detail = await managerState(page, name);
  await expect(detail.getByTestId('connection-state')).toHaveText('open (read-only)');
  await expect(detail.getByRole('button', { name: 'Disconnect', exact: true })).toBeVisible();
  await expect(header(page)).toContainText(`${name}, connected`);
}

test('setup: a DEV and a PROD profile', async ({ page }) => {
  await signIn(page);
  await addProfile(page, 'shop-dev', 'dev');
  await addProfile(page, 'shop-prod', 'prod');
});

test('DEV: Run after Disconnect opens nothing; Reconnect opens the connection and runs the query', async ({
  page,
}) => {
  const violations = await signIn(page);
  await runThenDisconnect(page, 'shop-dev', false);
  const writes = watchWrites(page);
  await runAndExpectNotice(page, 'shop-dev', writes);

  const ran = await historyCount(page);
  await banner(page).getByRole('button', { name: 'Reconnect' }).click();
  // DEV: no D-02. One open, then the query the user asked for.
  await expect(page.getByTestId('result-status')).toHaveText(/^1 row/);
  expect(writes).toEqual(['POST /api/connections', 'POST /api/queries']);
  await expect(production(page)).toHaveCount(0);
  await expect.poll(() => historyCount(page)).toBe(ran + 1);
  await expectRanAndConnected(page, 'shop-dev');
  await expectNoViolations(page, violations);
});

test('PROD: Reconnect asks D-02 again; Cancel leaves it disconnected; Connect runs the query', async ({
  page,
}) => {
  const violations = await signIn(page);
  await runThenDisconnect(page, 'shop-prod', true);
  const writes = watchWrites(page);
  await runAndExpectNotice(page, 'shop-prod', writes);
  await expect(banner(page).getByTestId('env-badge')).toHaveAttribute('data-env', 'prod');

  // The order: the banner, then D-02 (confirmed once already in this page: asked again), then the open and the run.
  await banner(page).getByRole('button', { name: 'Reconnect' }).click();
  await expect(production(page).getByRole('button', { name: 'Cancel' })).toBeFocused();
  await production(page).getByRole('button', { name: 'Cancel' }).click();
  await expect(production(page)).toHaveCount(0);
  await expect(banner(page)).toBeVisible();
  await expect(header(page)).toContainText('No connection');
  expect(writes).toEqual([]);
  expect(await openOnServer(page, 'user:shop-prod')).toEqual([]);

  await banner(page).getByRole('button', { name: 'Reconnect' }).click();
  await production(page).getByRole('button', { name: 'Connect' }).click();
  await expect(page.getByTestId('result-status')).toHaveText(/^1 row/);
  expect(writes).toEqual(['POST /api/connections', 'POST /api/queries']);
  // D-02 once: the run itself does not ask again.
  await expect(production(page)).toHaveCount(0);
  await expectRanAndConnected(page, 'shop-prod');
  await expectNoViolations(page, violations);
});

test('a loss is not a disconnect: a DEV connection the server closed is opened again by itself, as before', async ({
  page,
}) => {
  const violations = await signIn(page);
  await openEditor(page, 'user:shop-dev');
  await page.getByTestId('editor-run').click();
  await expect(page.getByTestId('result-status')).toHaveText(/^1 row/);
  await expect(header(page)).toContainText('shop-dev, connected');
  // Closed on the server, as another tab's "Disconnect" or a restart of the connection would: this page did not ask.
  for (const id of await openOnServer(page, 'user:shop-dev')) {
    expect((await api(page, 'DELETE', '/api/connections', { connection_id: id })).status).toBe(204);
  }
  const writes = watchWrites(page);
  await page.getByTestId('editor-run').click();
  await expect(page.getByTestId('result-status')).toHaveText(/^1 row/);
  // The query on the closed connection, the open of a new one, the query again; no question, no lasting banner.
  expect(writes).toEqual(['POST /api/queries', 'POST /api/connections', 'POST /api/queries']);
  await expect(banner(page)).toHaveCount(0);
  await expect(header(page)).toContainText('shop-dev, connected');
  expect(await openOnServer(page, 'user:shop-dev')).toHaveLength(1);
  await expectNoViolations(page, violations);
});

/** Every request on connections from now on (opens and requests on an open connection), as "METHOD path". */
function watchConnections(page: Page): string[] {
  const seen: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/connections') && request.method() !== 'GET') {
      seen.push(`${request.method()} ${path}`);
    } else if (path.startsWith('/api/connections/')) {
      seen.push(
        `${request.method()} ${path.replace(/^\/api\/connections\/[^/]+/, '/api/connections/{id}')}`,
      );
    }
  });
  return seen;
}

/** "Connect" and then "Disconnect" in the connection manager (D-02 for PROD on Connect). */
async function connectThenDisconnect(page: Page, name: string, prod: boolean): Promise<void> {
  // An earlier test may have left a connection of the profile open on the server (another page session).
  for (const id of await openOnServer(page, `user:${name}`)) {
    expect((await api(page, 'DELETE', '/api/connections', { connection_id: id })).status).toBe(204);
  }
  const detail = await managerState(page, name);
  await detail.getByRole('button', { name: 'Connect', exact: true }).click();
  if (prod) await production(page).getByRole('button', { name: 'Connect' }).click();
  await expect(detail.getByTestId('connection-state')).toHaveText('open (read-only)');
  await expect(header(page)).toContainText(`${name}, connected`);
  await detail.getByRole('button', { name: 'Disconnect', exact: true }).click();
  await expect(detail.getByTestId('connection-state')).toHaveText('not open');
  await expect(header(page)).toContainText('No connection');
  expect(await openOnServer(page, `user:${name}`)).toEqual([]);
}

const VIEWS = [
  { link: 'Migrations', button: 'Show status', result: 'migrations-result', prod: false },
  { link: 'Vector data', button: 'Inspect', result: 'vectors-report', prod: true },
  { link: 'Diagnostics', button: 'Run diagnosis', result: 'diagnostics-diagnosis', prod: false },
] as const;

for (const view of VIEWS) {
  const name = view.prod ? 'shop-prod' : 'shop-dev';
  test(`${view.link} "${view.button}" after Disconnect opens nothing; Reconnect opens it and goes on (${view.prod ? 'PROD' : 'DEV'})`, async ({
    page,
  }) => {
    const violations = await signIn(page);
    await connectThenDisconnect(page, name, view.prod);
    const seen = watchConnections(page);

    await page.getByRole('link', { name: view.link, exact: true }).click();
    await page.getByRole('main').getByRole('combobox').first().selectOption(`user:${name}`);
    await page.getByRole('button', { name: view.button, exact: true }).click();
    await expect(banner(page)).toHaveAttribute('data-cause', 'disconnected');
    await expect(banner(page)).toContainText(`Disconnected: ${name}`);
    await expect(production(page)).toHaveCount(0);
    await expect(header(page)).toContainText('No connection');
    // A second press asks again, with the one banner.
    await page.getByRole('button', { name: view.button, exact: true }).click();
    await expect(banner(page)).toHaveCount(1);
    // Nothing was opened and nothing was read on a connection.
    expect(seen).toEqual([]);
    expect(await openOnServer(page, `user:${name}`)).toEqual([]);
    // The view shows no result (its result area is empty or not there).
    await expect(page.getByTestId(view.result).filter({ hasText: /\S/ })).toHaveCount(0);

    await banner(page).getByRole('button', { name: 'Reconnect' }).click();
    if (view.prod) {
      // PROD: D-02 again; Cancel keeps it disconnected and opens nothing.
      await production(page).getByRole('button', { name: 'Cancel' }).click();
      await expect(banner(page)).toBeVisible();
      expect(seen).toEqual([]);
      expect(await openOnServer(page, `user:${name}`)).toEqual([]);
      await banner(page).getByRole('button', { name: 'Reconnect' }).click();
      await production(page).getByRole('button', { name: 'Connect' }).click();
    }
    await expect(banner(page)).toHaveCount(0);
    await expect(page.getByTestId(view.result).filter({ hasText: /\S/ })).toBeVisible();
    // The banner's open came first; then the view went on (its result shows).
    expect(seen[0]).toBe('POST /api/connections');
    expect(await openOnServer(page, `user:${name}`)).toHaveLength(1);
    await expect(header(page)).toContainText(`${name}, connected`);
    await expect(production(page)).toHaveCount(0);
    await expectNoViolations(page, violations);
  });
}
