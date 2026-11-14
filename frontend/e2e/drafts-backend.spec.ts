/**
 * TD-033 drafts part, review F-5, against a real `tabrion-studio` process: the SQL editor's drafts live in Studio's
 * state database behind the authenticated API, never in browser storage. A draft survives a reload and a restart of
 * Studio on another port (another browser origin); its text is in `state.sqlite` and in no `localStorage`,
 * `sessionStorage` or IndexedDB of the page; drafts an earlier version left in `localStorage` are moved to the server
 * once and removed. Under the strict CSP, with axe in light and dark, English and German.
 *
 * Runs only when TABRION_STUDIO_E2E_PYTHON names a Python in which Studio is installed with its built frontend, like
 * e2e/history-backend.spec.ts.
 */
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { test, expect, type Page } from '@playwright/test';

import { draftsKey } from '../src/views/editor/drafts';
import { expectNoViolations, recordViolations, seriousViolations } from './support';

const python = process.env.TABRION_STUDIO_E2E_PYTHON;
test.skip(!python, 'set TABRION_STUDIO_E2E_PYTHON to run against a real tabrion-studio');
test.describe.configure({ mode: 'serial' });

const NO_KEYRING_ENV = { ...process.env, PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring' };
const LINK = /do not share\): (http:\/\/127(?:\.\d{1,3}){3}:\d+\/launch#\S+)/;
/** A text only the drafts hold. */
const CANARY = `draft_canary_${Date.now().toString(36)}`;
const LEGACY = `legacy_canary_${Date.now().toString(36)}`;

let studio: ChildProcess | undefined;
let root = '';

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

/** Start Studio and wait for its sign-in link (a new port at every start). */
async function start(): Promise<string> {
  studio = spawn(python as string, args(), {
    stdio: ['ignore', 'pipe', 'inherit'],
    env: NO_KEYRING_ENV,
  });
  return new Promise<string>((resolve, reject) => {
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
}

async function stop(): Promise<void> {
  const running = studio;
  studio = undefined;
  if (running === undefined || running.exitCode !== null) return;
  await new Promise<void>((resolve) => {
    running.once('exit', () => resolve());
    running.kill('SIGTERM');
  });
}

let link = '';

test.beforeAll(async () => {
  root = mkdtempSync(join(tmpdir(), 'studio-drafts-e2e-'));
  mkdirSync(join(root, 'project'));
  link = await start();
});

test.afterAll(async () => {
  await stop();
  rmSync(root, { recursive: true, force: true });
});

function newLink(): string {
  const out = execFileSync(python as string, args(), { encoding: 'utf-8', env: NO_KEYRING_ENV });
  const match = LINK.exec(out);
  if (!match?.[1]) throw new Error(`no sign-in link: ${out}`);
  return match[1];
}

async function signIn(page: Page): Promise<string[]> {
  const violations = await recordViolations(page);
  const next = link;
  link = '';
  await page.goto(next || newLink());
  await expect(page.getByTestId('backend-status')).toContainText('Local server');
  return violations;
}

async function openEditor(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'SQL editor' }).click();
  await expect(page.locator('.cm-content')).toBeVisible();
}

/** The appearance choices in Studio's `settings.toml`, as the server reads them now. */
async function savedAppearance(page: Page): Promise<{ language?: string; theme?: string }> {
  return page.evaluate(async () => {
    const response = await fetch('/api/settings', {
      headers: { 'X-Tabrion-CSRF': sessionStorage.getItem('tabrion-studio.csrf') ?? '' },
    });
    const body = (await response.json()) as { settings?: { language?: string; theme?: string } };
    return { language: body.settings?.language, theme: body.settings?.theme };
  });
}

/** Wait for the next `PUT /api/drafts` that the server accepted. */
/**
 * The first successful draft save whose body holds `text`. Other saves (the editor's first one when it opens, a flush of
 * an earlier change) can answer first, so matching any PUT made the state-file check below race the save it meant.
 */
function draftSaved(page: Page, text: string) {
  return page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/drafts') &&
      response.request().method() === 'PUT' &&
      (response.request().postData() ?? '').includes(text) &&
      response.status() === 200,
  );
}

/** Everything the page's origin keeps in browser storage: both storages' keys and values, and IndexedDB's names. */
async function browserStorage(page: Page): Promise<string> {
  return page.evaluate(async () => {
    const all: string[] = [];
    for (const storage of [localStorage, sessionStorage]) {
      for (let i = 0; i < storage.length; i += 1) {
        const key = storage.key(i) ?? '';
        all.push(`${key}=${storage.getItem(key) ?? ''}`);
      }
    }
    const databases = 'databases' in indexedDB ? await indexedDB.databases() : [];
    for (const database of databases) all.push(`indexedDB:${database.name ?? ''}`);
    return all.join('\n');
  });
}

function stateFiles(): string[] {
  const folder = join(root, 'state');
  return readdirSync(folder)
    .filter((name) => name.startsWith('state.sqlite'))
    .map((name) => join(folder, name));
}

test('a draft survives a reload and a restart on another port, and is in no browser storage', async ({
  page,
}) => {
  const violations = await signIn(page);
  await openEditor(page);
  const saved = draftSaved(page, CANARY);
  await page.locator('.cm-content').click();
  await page.keyboard.insertText(`select '${CANARY}' as c`);
  await saved;
  // Nothing in browser storage holds the text, and no draft key exists.
  const stored = await browserStorage(page);
  expect(stored).not.toContain(CANARY);
  expect(stored).not.toContain('editor-drafts');
  expect(stored).not.toContain('indexedDB:');
  // The state database (0600) holds it.
  expect(stateFiles().some((file) => readFileSync(file).includes(CANARY))).toBe(true);

  await page.reload();
  await expect(page.locator('.cm-content')).toHaveText(`select '${CANARY}' as c`);
  expect(await browserStorage(page)).not.toContain(CANARY);

  // In the browser, a draft belonged to the port; in the state database it outlives a restart on another port.
  const before = new URL(page.url()).port;
  await stop();
  link = await start();
  await signIn(page);
  expect(new URL(page.url()).port).not.toBe(before);
  await openEditor(page);
  await expect(page.locator('.cm-content')).toHaveText(`select '${CANARY}' as c`);
  expect(await browserStorage(page)).not.toContain(CANARY);

  for (const [command, language] of [
    ['theme Light', 'en'],
    ['theme Dark', 'en'],
    ['Deutsch', 'de'],
    ['Darstellung: Hell', 'de'],
  ] as const) {
    await page.getByTestId('open-palette').click();
    await page.keyboard.type(command);
    await page.keyboard.press('Enter');
    await expect(page.locator('html')).toHaveAttribute('lang', language);
    expect(await seriousViolations(page)).toEqual([]);
  }
  await page.getByTestId('open-palette').click();
  await page.keyboard.type('English');
  await page.keyboard.press('Enter');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  // Each choice is applied at once and saved to settings.toml after the ones before it. The next test's page reads
  // that file, so this test ends only when its last choice is on the server: otherwise a save still queued when the
  // page closes leaves "Deutsch" behind, and the next sign-in is in German.
  await expect.poll(() => savedAppearance(page)).toEqual({ language: 'en', theme: 'light' });
  await expectNoViolations(page, violations);
});

test("this project's drafts an earlier version kept in localStorage move to the server once and leave the browser", async ({
  page,
}) => {
  const violations = await signIn(page);
  const project = realpathSync(join(root, 'project'));
  const key = await page.evaluate(
    ({ key, legacy }) => {
      localStorage.setItem(
        key,
        JSON.stringify({
          tabs: [
            {
              id: 'legacy1',
              title: 'Old draft',
              profile_id: '',
              sql: `select '${legacy}'`,
              saved_sql: '',
            },
          ],
          activeTab: 'legacy1',
        }),
      );
      // Another project's draft on this port: left for that project (its folder cannot be told from the key).
      localStorage.setItem(
        'tabrion-studio.editor-drafts.otherproject',
        '{"tabs":[],"activeTab":""}',
      );
      return key;
    },
    { key: draftsKey(project), legacy: LEGACY },
  );
  expect(key).toMatch(/^tabrion-studio\.editor-drafts\./);
  const saved = draftSaved(page, LEGACY);
  await openEditor(page);
  await expect(page.getByTestId('editor-tab').filter({ hasText: 'Old draft' })).toHaveCount(1);
  await saved;
  // This project's key goes; another project's key waits for that project's editor (review finding 3).
  await expect.poll(() => browserStorage(page)).not.toContain(key);
  expect(await browserStorage(page)).toContain('tabrion-studio.editor-drafts.otherproject');
  expect(await browserStorage(page)).not.toContain(LEGACY);
  expect(stateFiles().some((file) => readFileSync(file).includes(LEGACY))).toBe(true);
  // After a reload, the draft comes from the server.
  await page.reload();
  await expect(page.getByTestId('editor-tab').filter({ hasText: 'Old draft' })).toHaveCount(1);
  expect(await browserStorage(page)).not.toContain(LEGACY);
  await expectNoViolations(page, violations);
});
