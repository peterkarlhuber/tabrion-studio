/**
 * STU-050, UX acceptance of the MVP (08 sections 4.1 and 5, `docs/architecture/ux-acceptance.md`): the flows that no
 * other suite runs end to end against a real `tabrion-studio`. "First start": the launch link, the project's trust
 * dialog D-01, the start page, and the rows of the first table, in under 60 s and without the documentation.
 * "Connect": a failed connection test says what to check. axe (colour contrast included) on every step.
 *
 * Runs when TABRION_STUDIO_E2E_PYTHON names a Python in which Studio is installed with its built frontend (CI sets
 * it; `docs/development/building.md`).
 */
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { test, expect } from '@playwright/test';

import {
  expectNoViolations,
  foreignRequests,
  recordViolations,
  seriousViolations,
} from './support';

const python = process.env.TABRION_STUDIO_E2E_PYTHON;
test.skip(!python, 'set TABRION_STUDIO_E2E_PYTHON to run against a real tabrion-studio');
test.describe.configure({ mode: 'serial' });

/** The real server never reaches the developer's keyring (I-1 of stu-008-review.md). */
const NO_KEYRING_ENV = { ...process.env, PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring' };

let studio: ChildProcess | undefined;
let root = '';
let base = '';
const links: string[] = [];

function project(): string {
  return join(root, 'project');
}

function studioArgs(): string[] {
  return [
    '-m',
    'tabrion_studio',
    project(),
    '--no-browser',
    '--print-url',
    '--state-dir',
    join(root, 'state'),
  ];
}

const LINK = /do not share\): (http:\/\/127(?:\.\d{1,3}){3}:\d+\/launch#\S+)/;

test.beforeAll(async () => {
  root = mkdtempSync(join(tmpdir(), 'studio-e2e-ux-'));
  mkdirSync(join(project(), 'data'), { recursive: true });
  mkdirSync(join(project(), '.tabrion'));
  // The project a developer starts Studio in: a SQLite database and a profile file, one profile whose file is
  // missing (the "Connect" flow's failure).
  execFileSync(python as string, [
    '-c',
    'import sqlite3, sys; c = sqlite3.connect(sys.argv[1]);' +
      ' c.execute("CREATE TABLE parts (id INTEGER PRIMARY KEY, material TEXT NOT NULL)");' +
      ' c.executemany("INSERT INTO parts (material) VALUES (?)", [("Aluminium",), ("Steel",), ("Brass",)]);' +
      ' c.commit(); c.close()',
    join(project(), 'data', 'parts.db'),
  ]);
  writeFileSync(
    join(project(), '.tabrion', 'studio.toml'),
    [
      'version = 1',
      '[[profile]]',
      'name = "parts-dev"',
      'engine = "sqlite"',
      'path = "data/parts.db"',
      'environment = "dev"',
      '[[profile]]',
      'name = "parts-moved"',
      'engine = "sqlite"',
      'path = "data/moved.db"',
      'environment = "test"',
      '',
    ].join('\n'),
  );
  studio = spawn(python as string, studioArgs(), {
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
  if (root) rmSync(root, { recursive: true, force: true });
});

/** A fresh one-time link from the running server (a second `tabrion-studio --print-url --no-browser`). */
function newLink(): string {
  const out = execFileSync(python as string, studioArgs(), {
    encoding: 'utf-8',
    env: NO_KEYRING_ENV,
  });
  const match = LINK.exec(out);
  if (!match?.[1]) throw new Error(`no sign-in link: ${out}`);
  return match[1];
}

test('08 section 5 "First start": D-01, the start page, the first table\'s rows in under 60 s', async ({
  page,
}) => {
  const violations = await recordViolations(page);
  const foreign = foreignRequests(page, () => base);
  const started = Date.now();
  await page.goto(links.shift() as string);
  // D-01: the project's profiles are used only after the user trusted them; the safe option has the focus.
  const trust = page.getByRole('dialog', { name: 'Trust the project profiles?' });
  await expect(trust).toBeVisible();
  await expect(trust.getByRole('button', { name: "Don't load" })).toBeFocused();
  await expect(trust.getByTestId('trust-connections').locator('li code')).toHaveText([
    'parts-dev',
    'parts-moved',
  ]);
  expect(await seriousViolations(page)).toEqual([]);
  await trust.getByRole('button', { name: 'Trust' }).click();
  await expect(trust).toBeHidden();
  // 08 section 4.1: the start page says what to do next, without the documentation.
  await expect(page.getByRole('heading', { level: 1, name: 'Welcome' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: 'Get started' })).toBeVisible();
  expect(await seriousViolations(page)).toEqual([]);
  await page.getByRole('link', { name: 'Browse tables and views' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Explorer' })).toBeVisible();
  await page.getByLabel('Connection profile').selectOption({ label: 'parts-dev (DEV)' });
  await page.getByTestId('explorer-open').click();
  await expect(page.getByTestId('explorer-message')).toContainText('Catalog read:');
  await page.getByRole('treeitem', { name: 'parts', exact: true }).click();
  await page.getByTestId('explorer-show-data').click();
  await expect(page.getByTestId('viewer-status')).toHaveText('Rows 1–3; this is the last page.');
  await expect(page.getByRole('grid').getByRole('gridcell').nth(1)).toHaveText('Aluminium');
  // The plan's measure: under 60 s from the launch link to the first table (the test itself is much faster).
  expect(Date.now() - started).toBeLessThan(60_000);
  await expect(page).toHaveTitle('[DEV] parts-dev – Tabrion Studio');
  expect(await seriousViolations(page)).toEqual([]);
  await expectNoViolations(page, violations);
  expect(foreign).toEqual([]);
});

test('08 section 5 "Connect": a failed connection test says what to check', async ({ page }) => {
  const violations = await recordViolations(page);
  await page.goto(newLink());
  await expect(page.getByTestId('backend-status')).toContainText('Local server');
  // The project stays trusted for the next page (the record is on the server).
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('link', { name: 'Add or test a connection profile' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Connections' })).toBeVisible();
  await page.getByTestId('connection-item').filter({ hasText: 'parts-moved' }).click();
  const detail = page.getByTestId('connection-detail');
  await detail.getByRole('button', { name: 'Test connection' }).click();
  await expect(detail.getByTestId('connection-outcome')).toHaveText(
    "Test failed: The profile's SQLite file does not exist. Check the file path in the profile under Connections.",
  );
  expect(await seriousViolations(page)).toEqual([]);
  // The remedy is right: the file at the profile's path makes the test pass.
  execFileSync(python as string, [
    '-c',
    'import sqlite3, sys; c = sqlite3.connect(sys.argv[1]); c.execute("CREATE TABLE t (x)"); c.commit(); c.close()',
    join(project(), 'data', 'moved.db'),
  ]);
  await detail.getByRole('button', { name: 'Test connection' }).click();
  await expect(detail.getByTestId('connection-outcome')).toContainText('Connection works:');
  await expectNoViolations(page, violations);
});
