/**
 * Finding A-01: every `tabrion-studio` start serves a fresh, unpredictable origin `http://127.a.b.c:<port>`, so
 * nothing another program left in the browser for an earlier origin on that port (a service worker, storage) applies
 * to a later Studio, also with a fixed `--port`.
 *
 * - All browsers: two starts on the same `--port` get two different loopback addresses; the second signs in.
 * - Chromium: a page that another local program served on `127.0.0.1:<port>` while Studio was not running registers a
 *   service worker for that origin, which answers every later page load of it. A Studio started afterwards on the same
 *   port is not reached through that worker: its launch link signs in to the real Studio, while the worker still holds
 *   the old origin. (Firefox and WebKit do not run that part: Playwright's service worker support is Chromium's.)
 *
 * Runs only when TABRION_STUDIO_E2E_PYTHON names a Python in which Studio is installed with its built frontend, like
 * e2e/backend.spec.ts. Linux and Windows route all of 127.0.0.0/8 to loopback; macOS configures only 127.0.0.1 by
 * default, where Studio falls back to it (the residual risk in STU-ADR-0002), so these tests do not apply there.
 */
import { spawn, type ChildProcess } from 'node:child_process';
import { createServer as createHttpServer, type Server } from 'node:http';
import { createServer as createNetServer } from 'node:net';
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { test, expect, type Page } from '@playwright/test';

const python = process.env.TABRION_STUDIO_E2E_PYTHON;
test.skip(!python, 'set TABRION_STUDIO_E2E_PYTHON to run against a real tabrion-studio');
test.skip(
  process.platform === 'darwin',
  'macOS configures only 127.0.0.1: the documented fallback of A-01 (STU-ADR-0002)',
);
test.describe.configure({ mode: 'serial' });

/** I-1 of stu-008-review.md: the real server never reaches the developer's keyring. */
const STUDIO_ENV = { ...process.env, PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring' };
const LINK = /do not share\): (http:\/\/127(?:\.\d{1,3}){3}:\d+\/launch#\S+)/;
const FOREIGN = 'Not Tabrion Studio';

let root = '';
const running: ChildProcess[] = [];

test.beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), 'studio-origin-e2e-'));
  mkdirSync(join(root, 'project'));
});

test.afterAll(async () => {
  for (const proc of running) await stop(proc);
  if (root) rmSync(root, { recursive: true, force: true });
});

/** A port that is free on 127.0.0.1 right now. */
async function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createNetServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      server.close(() => {
        if (address !== null && typeof address === 'object') resolve(address.port);
        else reject(new Error('no port'));
      });
    });
  });
}

/** Start `tabrion-studio --port <port>` and return the process with its one-time sign-in link. */
async function start(port: number): Promise<{ proc: ChildProcess; link: string }> {
  const proc = spawn(
    python as string,
    [
      '-m',
      'tabrion_studio',
      join(root, 'project'),
      '--port',
      String(port),
      '--no-browser',
      '--print-url',
      '--state-dir',
      join(root, 'state'),
    ],
    { stdio: ['ignore', 'pipe', 'inherit'], env: STUDIO_ENV },
  );
  running.push(proc);
  const link = await new Promise<string>((resolve, reject) => {
    let out = '';
    const timer = setTimeout(() => reject(new Error(`no sign-in link: ${out}`)), 30_000);
    proc.stdout?.on('data', (chunk: Buffer) => {
      out += chunk.toString();
      const match = LINK.exec(out);
      if (match?.[1]) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
  });
  return { proc, link };
}

/** Stop a Studio and wait until it has exited, so that its port is free again. */
async function stop(proc: ChildProcess): Promise<void> {
  if (proc.exitCode !== null || proc.signalCode !== null) return;
  const exited = new Promise<void>((resolve) => proc.once('exit', () => resolve()));
  proc.kill('SIGTERM');
  await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 10_000))]);
}

async function signIn(page: Page, link: string): Promise<void> {
  await page.goto(link);
  await expect(page.getByTestId('backend-status')).toContainText('Local server');
  await expect(page.getByRole('heading', { level: 1, name: 'Welcome' })).toBeVisible();
}

/**
 * Another program on `127.0.0.1:<port>`: its page registers a service worker for the whole origin that answers every
 * page load with its own page. Studio never registers one.
 */
async function foreignServer(port: number): Promise<Server> {
  const worker = `
    self.addEventListener('install', () => self.skipWaiting());
    self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
    self.addEventListener('fetch', (event) => {
      if (event.request.mode === 'navigate') {
        event.respondWith(new Response('<!doctype html><title>${FOREIGN}</title><p>${FOREIGN}</p>',
          { headers: { 'Content-Type': 'text/html' } }));
      }
    });`;
  const page =
    '<!doctype html><title>Other program</title><p id="state">waiting</p><script src="/register.js"></script>';
  const register = `navigator.serviceWorker.register('/worker.js', { scope: '/' })
    .then(() => navigator.serviceWorker.ready)
    .then(() => { document.getElementById('state').textContent = 'registered'; })
    .catch((error) => { document.getElementById('state').textContent = 'failed: ' + error; });`;
  const server = createHttpServer((request, response) => {
    const [type, body] =
      request.url === '/worker.js'
        ? ['text/javascript', worker]
        : request.url === '/register.js'
          ? ['text/javascript', register]
          : ['text/html', page];
    response.writeHead(200, { 'Content-Type': type, 'Cache-Control': 'no-store' });
    response.end(body);
  });
  await new Promise<void>((resolve) => server.listen(port, '127.0.0.1', resolve));
  return server;
}

test('every start on a fixed port gets a fresh loopback address', async ({ page }) => {
  const port = await freePort();
  const first = await start(port);
  const before = new URL(first.link);
  await stop(first.proc);
  const second = await start(port);
  const after = new URL(second.link);
  expect(before.port).toBe(String(port));
  expect(after.port).toBe(String(port));
  for (const host of [before.hostname, after.hostname]) {
    expect(host).toMatch(/^127(?:\.\d{1,3}){3}$/);
    expect(host).not.toBe('127.0.0.1');
  }
  expect(after.hostname).not.toBe(before.hostname);
  await signIn(page, second.link);
  await expect(page).toHaveURL(`${after.origin}/`);
  await stop(second.proc);
});

test("another program's service worker on the port does not reach a later Studio", async ({
  page,
  browserName,
}) => {
  test.skip(
    browserName !== 'chromium',
    "chromium only: Playwright's service worker support is Chromium's",
  );
  const port = await freePort();
  const old = `http://127.0.0.1:${port}`;
  // While Studio does not run, the browser visits a page of another program on the port once.
  const foreign = await foreignServer(port);
  await page.goto(`${old}/`);
  await expect(page.locator('#state')).toHaveText('registered');
  await new Promise<void>((resolve) => foreign.close(() => resolve()));
  // The worker now answers that origin, with nothing listening on it any more.
  await page.goto(`${old}/launch`);
  await expect(page).toHaveTitle(FOREIGN);
  // Studio starts later on the same port: its launch link reaches the real Studio, never the worker.
  const studio = await start(port);
  const origin = new URL(studio.link).origin;
  expect(origin).not.toBe(old);
  await signIn(page, studio.link);
  await expect(page).not.toHaveTitle(FOREIGN);
  const health = await page.evaluate(async () => (await fetch('/api/health')).status);
  expect(health).toBe(200);
  await stop(studio.proc);
});
