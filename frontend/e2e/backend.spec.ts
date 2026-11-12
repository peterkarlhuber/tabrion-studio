/**
 * The built frontend in a real `tabrion-studio` process: the launch link signs in, the page renders under the strict
 * Content-Security-Policy of STU-003 without a single violation, the brand assets are served, a reload on a view path
 * works, and axe (colour contrast included) finds no serious or critical violation in light and dark.
 *
 * Runs only when TABRION_STUDIO_E2E_PYTHON names a Python in which Studio is installed with its built frontend
 * (`python scripts/build_frontend.py` and `pip install -e .`, or the wheel).
 */
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import {
  chmodSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { test, expect, type Page } from '@playwright/test';

import { foreignRequests, seriousViolations } from './support';

const python = process.env.TABRION_STUDIO_E2E_PYTHON;
test.skip(!python, 'set TABRION_STUDIO_E2E_PYTHON to run against a real tabrion-studio');
test.describe.configure({ mode: 'serial' });

/** I-1 of stu-008-review.md: the real server never reaches the developer's keyring (null backend: "no keyring"). */
const NO_KEYRING_ENV = { ...process.env, PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring' };

let studio: ChildProcess | undefined;
let base = '';
let root = '';
const links: string[] = [];

test.beforeAll(async () => {
  root = mkdtempSync(join(tmpdir(), 'studio-e2e-'));
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
  const first = await new Promise<string>((resolve, reject) => {
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
  links.push(first);
  base = new URL(first).origin;
});

test.afterAll(() => {
  studio?.kill('SIGTERM');
});

/** A fresh one-time link from the running server: a second `tabrion-studio --print-url --no-browser`. */
function newLink(): string {
  const out = execFileSync(
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
    { encoding: 'utf-8', env: NO_KEYRING_ENV },
  );
  const match = /do not share\): (http:\/\/127\.0\.0\.1:\d+\/launch#\S+)/.exec(out);
  if (!match?.[1]) throw new Error(`no sign-in link: ${out}`);
  return match[1];
}

async function signIn(page: Page): Promise<string[]> {
  const violations: string[] = [];
  page.on('console', (message) => {
    if (/Content Security Policy|Refused to/i.test(message.text())) violations.push(message.text());
  });
  page.on('pageerror', (error) => violations.push(String(error)));
  const link = links.shift();
  expect(link, 'one sign-in link per test').toBeDefined();
  await page.goto(link as string);
  await expect(page.getByTestId('backend-status')).toContainText('Local server');
  return violations;
}

test('the launch link signs in and the page runs under the CSP', async ({ page }) => {
  const violations = await signIn(page);
  await expect(page).toHaveURL(`${base}/`);
  await expect(page.getByRole('heading', { level: 1, name: 'Welcome' })).toBeVisible();
  // Favicon and logo come from the server (img-src 'self').
  const logo = page.getByRole('img', { name: 'Tabrion Studio' });
  await expect(logo).toBeVisible();
  expect(await logo.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  expect((await page.request.get(`${base}/branding/favicon.ico`)).status()).toBe(200);
  // A view path survives a reload (CLIENT_ROUTES); the session lives in this tab's sessionStorage.
  await page.getByRole('link', { name: 'Settings' }).click();
  await page.reload();
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
  for (const theme of ['light', 'dark'] as const) {
    await page.getByRole('radio', { name: theme === 'light' ? 'Light' : 'Dark' }).check();
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    expect(await seriousViolations(page)).toEqual([]);
  }
  expect(violations).toEqual([]);
});

test('STU-006: D-01 asks before the project profiles are used, and trusting sticks', async ({
  page,
}) => {
  // A project file with a hostile name, a host and a password variable (T-08).
  mkdirSync(join(root, 'project', '.tabrion'), { recursive: true });
  writeFileSync(
    join(root, 'project', '.tabrion', 'studio.toml'),
    [
      'version = 1',
      '[[profile]]',
      'name = "parts-dev, safe (checked)"',
      'engine = "postgresql"',
      'host = "db.internal"',
      'database = "parts"',
      'password = "env:PGPASSWORD"',
      'environment = "dev"',
      '',
    ].join('\n'),
  );
  links.push(newLink());
  const violations = await signIn(page);
  const dialog = page.getByRole('dialog', { name: 'Trust the project profiles?' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('button', { name: "Don't load" })).toBeFocused();
  await expect(dialog.getByTestId('trust-connections').locator('li code')).toHaveText([
    'parts-dev, safe (checked)',
  ]);
  await expect(dialog.getByTestId('trust-hosts').locator('li code')).toHaveText([
    'db.internal:5432',
  ]);
  await expect(dialog.getByTestId('trust-env').locator('li code')).toHaveText(['PGPASSWORD']);
  expect(await seriousViolations(page)).toEqual([]);
  const profiles = async () =>
    page.evaluate(async () => {
      const csrf = sessionStorage.getItem('tabrion-studio.csrf') ?? '';
      const response = await fetch('/api/profiles', { headers: { 'X-Tabrion-CSRF': csrf } });
      return ((await response.json()) as { profiles: { source: string }[] }).profiles.filter(
        (p) => p.source === 'project',
      ).length;
    });
  expect(await profiles()).toBe(0);
  await dialog.getByRole('button', { name: 'Trust' }).click();
  await expect(dialog).toBeHidden();
  expect(await profiles()).toBe(1);
  await page.reload();
  await expect(page.getByTestId('backend-status')).toContainText('Local server');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(violations).toEqual([]);
});

test('STU-008: a profile created in the page is written to profiles.toml and deleted again', async ({
  page,
}) => {
  links.push(newLink());
  const violations = await signIn(page);
  // The trusted project of the previous test may still ask; this test is about the user's own profiles.
  await page.getByRole('link', { name: 'Connections' }).click();
  await page.getByRole('button', { name: 'New connection' }).click();
  const dialog = page.getByRole('dialog', { name: 'New connection' });
  await dialog.getByRole('textbox', { name: 'File' }).fill(join(root, 'parts.db'));
  await dialog.getByRole('button', { name: 'Next' }).click();
  await dialog.getByRole('button', { name: 'Next' }).click();
  await dialog.getByLabel('Name').fill('e2e-parts');
  await dialog.getByRole('radio', { name: 'DEV' }).check();
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByTestId('connections-status')).toHaveText('e2e-parts saved.');
  const file = join(root, 'state', 'config', 'profiles.toml');
  expect(readFileSync(file, 'utf-8')).toContain('name = "e2e-parts"');
  if (process.platform !== 'win32') expect(statSync(file).mode & 0o777).toBe(0o600);
  expect(await seriousViolations(page)).toEqual([]);
  await page.getByTestId('connection-item').filter({ hasText: 'e2e-parts' }).click();
  await page.getByRole('button', { name: 'Delete' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByTestId('connections-status')).toHaveText('e2e-parts deleted.');
  expect(readFileSync(file, 'utf-8')).not.toContain('e2e-parts');
  expect(violations).toEqual([]);
});

test('STU-014: the page runs the diagnosis of a prepared SQLite file and shows its findings', async ({
  page,
}) => {
  // 09 backlog: rows written with foreign keys off (STU-DX-005) and, on POSIX, a 0644 file (TAB-SQ-003).
  const file = join(root, 'diag-shop.db');
  execFileSync(python as string, [
    '-c',
    [
      'import sqlite3, sys',
      'c = sqlite3.connect(sys.argv[1])',
      'c.executescript("CREATE TABLE supplier (id INTEGER PRIMARY KEY);' +
        ' CREATE TABLE part (id INTEGER PRIMARY KEY, supplier_id INTEGER REFERENCES supplier (id));' +
        ' INSERT INTO part VALUES (1, 99);")',
      'c.commit()',
      'c.close()',
    ].join('\n'),
    file,
  ]);
  if (process.platform !== 'win32') chmodSync(file, 0o644);
  links.push(newLink());
  const violations = await signIn(page);
  await page.getByRole('link', { name: 'Connections' }).click();
  await page.getByRole('button', { name: 'New connection' }).click();
  const wizard = page.getByRole('dialog', { name: 'New connection' });
  await wizard.getByRole('textbox', { name: 'File' }).fill(file);
  await wizard.getByRole('button', { name: 'Next' }).click();
  await wizard.getByRole('button', { name: 'Next' }).click();
  await wizard.getByLabel('Name').fill('e2e-diag');
  await wizard.getByRole('radio', { name: 'DEV' }).check();
  await wizard.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByTestId('connections-status')).toHaveText('e2e-diag saved.');

  await page.getByRole('link', { name: 'Diagnostics' }).click();
  await page.getByLabel('Connection profile').selectOption({ label: 'e2e-diag (DEV)' });
  await page.getByRole('button', { name: 'Run diagnosis' }).click();
  const list = page.getByTestId('diagnostics-findings');
  await expect(list).toBeVisible();
  const codes = await page.getByTestId('diagnostics-code').allTextContents();
  expect(codes).toContain('STU-DX-005');
  if (process.platform !== 'win32') expect(codes).toContain('TAB-SQ-003');
  // Studio's own session findings are left out, and no finding names the file (03 section 6).
  expect(codes).not.toContain('TAB-SQ-001');
  await expect(page.getByTestId('diagnostics-result')).not.toContainText('diag-shop');
  await expect(page.getByTestId('diagnostics-incomplete')).toHaveCount(0);
  expect(await seriousViolations(page)).toEqual([]);
  expect(violations).toEqual([]);
});

test('QA-9, SQ-08: no request leaves the local server', async ({ page }) => {
  const foreign = foreignRequests(page, () => base);
  const seen: string[] = [];
  page.on('request', (request) => seen.push(new URL(request.url()).pathname));
  links.push(newLink());
  const violations = await signIn(page);
  for (const name of ['Connections', 'Settings', 'Start']) {
    await page.getByRole('link', { name }).click();
    await page.waitForLoadState('networkidle');
  }
  await page.reload();
  await page.waitForLoadState('networkidle');
  // The page loaded its script, styles, fonts and logo, and called the API: all from 127.0.0.1.
  expect(seen).toContain('/api/health');
  expect(seen.some((path) => path.startsWith('/assets/'))).toBe(true);
  expect(foreign).toEqual([]);
  expect(violations).toEqual([]);
});

test('TD-021: language, theme and density are kept in settings.toml for the next browser', async ({
  page,
  browser,
}) => {
  links.push(newLink());
  const violations = await signIn(page);
  await page.getByRole('link', { name: 'Settings' }).click();
  await page.getByRole('radio', { name: 'Normal' }).check();
  await expect(page.getByTestId('appearance-sync')).toHaveText(/Saved in settings.toml/);
  await page.getByRole('radio', { name: 'Dark' }).check();
  await page.getByRole('radio', { name: 'Deutsch' }).check();
  await expect(page.getByRole('heading', { level: 1, name: 'Einstellungen' })).toBeVisible();
  const file = join(root, 'state', 'config', 'settings.toml');
  await expect
    .poll(() => {
      try {
        return readFileSync(file, 'utf-8');
      } catch {
        return '';
      }
    })
    .toMatch(/language = "de"\ntheme = "dark"\ndensity = "normal"\n/);
  if (process.platform !== 'win32') expect(statSync(file).mode & 0o777).toBe(0o600);
  expect(violations).toEqual([]);

  // A browser without any stored preference (as after a restart on another port) gets them from the file.
  const fresh = await browser.newContext({ colorScheme: 'light', locale: 'en-US' });
  const other = await fresh.newPage();
  links.push(newLink());
  await other.goto(links.shift() as string);
  await expect(other.getByRole('heading', { level: 1, name: 'Willkommen' })).toBeVisible();
  await expect(other.locator('html')).toHaveAttribute('lang', 'de');
  await expect(other.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(other.locator('html')).toHaveAttribute('data-density', 'normal');
  await fresh.close();
  // L-6 of stu-052-td021-review.md: later tests sign in to an English page with the default look.
  rmSync(file, { force: true });
});
