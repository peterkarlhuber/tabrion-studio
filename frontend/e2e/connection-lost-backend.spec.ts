/**
 * TD-034 end to end: the banner "Connection lost" with "Reconnect" in a real `tabrion-studio` process under the strict
 * Content-Security-Policy. The connection is closed on the server while the explorer shows it (DEV and PROD; PROD asks
 * D-02 again on "Reconnect"), and, when TABRION_STUDIO_TEST_POSTGRES and TABRION_STUDIO_TEST_POSTGRES_SUPERUSER name a
 * throwaway PostgreSQL database, the database itself stops answering (`ALLOW_CONNECTIONS false` and Studio's sessions
 * ended): "Reconnect" fails while it is down and reads the catalog again once it is back.
 *
 * Runs only when TABRION_STUDIO_E2E_PYTHON names a Python in which Studio is installed with its built frontend, like
 * e2e/viewer-backend.spec.ts.
 */
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { test, expect, type Page } from '@playwright/test';

import { seriousViolations } from './support';

const python = process.env.TABRION_STUDIO_E2E_PYTHON;
test.skip(!python, 'set TABRION_STUDIO_E2E_PYTHON to run against a real tabrion-studio');
test.describe.configure({ mode: 'serial' });

const postgres = process.env.TABRION_STUDIO_TEST_POSTGRES;
const superuser = process.env.TABRION_STUDIO_TEST_POSTGRES_SUPERUSER;
const PASSWORD_VARIABLE = 'TABRION_STUDIO_E2E_PGPASSWORD';

/** I-1 of stu-008-review.md: the real server never reaches the developer's keyring. */
const STUDIO_ENV = {
  ...process.env,
  PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring',
  [PASSWORD_VARIABLE]: postgres ? decodeURIComponent(new URL(postgres).password) : '',
};
const LINK = /do not share\): (http:\/\/127\.0\.0\.1:\d+\/launch#\S+)/;

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
  root = mkdtempSync(join(tmpdir(), 'studio-lost-e2e-'));
  mkdirSync(join(root, 'project'));
  database = join(root, 'parts.db');
  execFileSync(python as string, [
    '-I',
    '-c',
    [
      'import sqlite3, sys',
      'db = sqlite3.connect(sys.argv[1])',
      "db.execute('create table parts (id integer primary key, material text)')",
      "db.execute('create table material (name text)')",
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
  const violations: string[] = [];
  page.on('console', (message) => {
    if (/Content Security Policy|Refused to/i.test(message.text())) violations.push(message.text());
  });
  page.on('pageerror', (error) => violations.push(String(error)));
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

async function addProfile(page: Page, profile: Record<string, unknown>): Promise<void> {
  const list = await api(page, 'GET', '/api/profiles');
  const digest = (list.doc as { user_file?: { digest?: string | null } }).user_file?.digest ?? null;
  expect(
    (await api(page, 'POST', '/api/profiles', { profile, digest, overwrite: false })).status,
  ).toBe(201);
}

/** Close every open connection of `profileId` on the server, as another tab's "Disconnect" would. */
async function closeOnServer(page: Page, profileId: string): Promise<void> {
  const open = await api(page, 'GET', '/api/connections');
  const ids = (
    open.doc as { connections: { connection_id: string; profile_id: string }[] }
  ).connections
    .filter((c) => c.profile_id === profileId)
    .map((c) => c.connection_id);
  expect(ids.length).toBeGreaterThan(0);
  for (const id of ids) {
    expect((await api(page, 'DELETE', '/api/connections', { connection_id: id })).status).toBe(204);
  }
}

async function openExplorer(page: Page, profile: string): Promise<void> {
  await page.getByRole('link', { name: 'Explorer' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Explorer' })).toBeVisible();
  await page.getByRole('combobox').first().selectOption(`user:${profile}`);
  await page.getByTestId('explorer-open').click();
}

const production = (page: Page) => page.getByRole('dialog', { name: 'Production database' });

test('setup: a DEV and a PROD profile', async ({ page }) => {
  await signIn(page);
  await addProfile(page, {
    name: 'e2e-lost',
    engine: 'sqlite',
    environment: 'dev',
    path: database,
  });
  await addProfile(page, {
    name: 'e2e-lost-prod',
    engine: 'sqlite',
    environment: 'prod',
    path: database,
  });
});

test('DEV: the connection closed on the server shows the banner; Reconnect reads the catalog again', async ({
  page,
}) => {
  const violations = await signIn(page);
  await openExplorer(page, 'e2e-lost');
  await expect(page.getByRole('treeitem', { name: 'parts', exact: true })).toBeVisible();
  await closeOnServer(page, 'user:e2e-lost');

  await page.getByTestId('explorer-refresh').click();
  const banner = page.getByTestId('connection-lost');
  await expect(banner).toContainText('Connection lost: e2e-lost');
  await expect(banner).toContainText('Studio no longer has this connection; it was closed.');
  await expect(banner).toContainText('Nothing was lost: Studio only reads.');
  expect(await seriousViolations(page)).toEqual([]);

  const before = (await api(page, 'GET', '/api/connections')).doc as { connections: unknown[] };
  await banner.getByRole('button', { name: 'Reconnect' }).click();
  await expect(page.getByTestId('connection-lost')).toHaveCount(0);
  await expect(production(page)).toHaveCount(0);
  await expect(page.getByRole('main')).toBeFocused();
  await expect(page.getByRole('treeitem', { name: 'parts', exact: true })).toBeVisible();
  const after = (await api(page, 'GET', '/api/connections')).doc as { connections: unknown[] };
  expect(after.connections.length).toBe(before.connections.length + 1);
  expect(violations).toEqual([]);
});

test('PROD: Reconnect asks D-02 again; Cancel keeps the banner and opens nothing', async ({
  page,
}) => {
  const violations = await signIn(page);
  await openExplorer(page, 'e2e-lost-prod');
  await production(page).getByRole('button', { name: 'Connect' }).click();
  await expect(page.getByRole('treeitem', { name: 'parts', exact: true })).toBeVisible();
  await closeOnServer(page, 'user:e2e-lost-prod');

  await page.getByTestId('explorer-refresh').click();
  const banner = page.getByTestId('connection-lost');
  await expect(banner).toContainText('Connection lost: e2e-lost-prod');
  await expect(banner.getByTestId('env-badge')).toHaveAttribute('data-env', 'prod');

  const opens: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/connections') {
      opens.push(request.postData() ?? '');
    }
  });
  await banner.getByRole('button', { name: 'Reconnect' }).click();
  await expect(production(page).getByRole('button', { name: 'Cancel' })).toBeFocused();
  await production(page).getByRole('button', { name: 'Cancel' }).click();
  await expect(production(page)).toHaveCount(0);
  await expect(banner).toBeVisible();
  expect(opens).toEqual([]);

  await banner.getByRole('button', { name: 'Reconnect' }).click();
  await production(page).getByRole('button', { name: 'Connect' }).click();
  await expect(page.getByTestId('connection-lost')).toHaveCount(0);
  await expect(page.getByRole('treeitem', { name: 'parts', exact: true })).toBeVisible();
  expect(opens.map((body) => JSON.parse(body) as unknown)).toEqual([
    { profile_id: 'user:e2e-lost-prod', confirmed_environment: 'prod' },
  ]);
  expect(violations).toEqual([]);
});

/** Make the throwaway database refuse connections and end Studio's sessions (`down`), or accept them again. */
function setDatabase(state: 'down' | 'up'): void {
  const target = new URL(postgres as string);
  const name = decodeURIComponent(target.pathname.slice(1));
  execFileSync(python as string, [
    '-I',
    '-c',
    [
      'import sys, psycopg',
      'from psycopg import sql',
      'url, name, state = sys.argv[1], sys.argv[2], sys.argv[3]',
      'with psycopg.connect(url, dbname="postgres", autocommit=True) as db:',
      '    allow = sql.SQL("true" if state == "up" else "false")',
      '    db.execute(sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS {}").format(sql.Identifier(name), allow))',
      '    if state == "down":',
      '        db.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity' +
        ' WHERE application_name = %s AND datname = %s", ("tabrion-studio", name))',
    ].join('\n'),
    superuser as string,
    name,
    state,
  ]);
}

test('PostgreSQL: the database stops answering; Reconnect fails while it is down', async ({
  page,
}) => {
  // Only with a throwaway PostgreSQL database (the CI e2e job has none): the SQLite tests above cover the banner.
  if (!postgres || !superuser) return;
  const target = new URL(postgres);
  const violations = await signIn(page);
  await addProfile(page, {
    name: 'e2e-lost-pg',
    engine: 'postgresql',
    environment: 'dev',
    host: target.hostname,
    port: Number(target.port || '5432'),
    database: decodeURIComponent(target.pathname.slice(1)),
    user: decodeURIComponent(target.username),
    sslmode: 'disable',
    password: `env:${PASSWORD_VARIABLE}`,
  });
  await page.reload();
  await expect(page.getByTestId('backend-status')).toContainText('Local server');
  await openExplorer(page, 'e2e-lost-pg');
  await expect(page.getByRole('tree')).toBeVisible();
  try {
    setDatabase('down');
    await page.getByTestId('explorer-refresh').click();
    const banner = page.getByTestId('connection-lost');
    await expect(banner).toContainText('Connection lost: e2e-lost-pg');
    await expect(banner).toContainText('The database does not answer.');
    await banner.getByRole('button', { name: 'Reconnect' }).click();
    await expect(page.getByTestId('connection-reconnect-problem')).toContainText(
      'Reconnecting failed: ',
    );
    await expect(banner).toBeVisible();
  } finally {
    setDatabase('up');
  }
  await page.getByTestId('connection-lost').getByRole('button', { name: 'Reconnect' }).click();
  await expect(page.getByTestId('connection-lost')).toHaveCount(0);
  await expect(page.getByRole('tree')).toBeVisible();
  expect(violations).toEqual([]);
});
