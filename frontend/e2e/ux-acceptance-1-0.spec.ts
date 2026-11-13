/**
 * STU-054, UX acceptance of Studio 1.0 without the Data Editor (09 STU-054: "Flows 1.0 E2E ohne Data Editor;
 * Schreibfunktionen gesperrt (Negativtest); de/en vollständig"; `docs/architecture/ux-acceptance.md`, section
 * "Studio 1.0"), against a real `tabrion-studio` under its strict CSP:
 *
 * - the 1.0 tour in English and in German: the DDL of a table, the ER diagram, a query with a parameter, Format, the
 *   export dialog, the query history, the migration status and a diagnosis; axe (colour contrast included) in light
 *   and dark at every step, and on the German pages no text of the English catalog and no raw i18n key;
 * - the negative tests of 03 section 9 ("Schreibfunktionen gesperrt: alle Schreibversuche über API ... (Negativtest)")
 *   and 08 section 5 ("PROD: Chip zeigt ⛔ mit Tooltip, kein Dialog"): the page offers no data editing (STU-026 stays
 *   locked behind G-S2), the API refuses every write on a DEV and on a PROD connection and the database file is
 *   byte for byte unchanged, and every changing API route is on a known list of routes that never write to a database.
 *
 * The other 1.0 acceptance flows run in their own suites and are not repeated here: favorites, history without
 * parameter values, scripts and completion in `history-backend.spec.ts`, the export files in `editor-backend.spec.ts`
 * (TD-043), the ER diagram's SVG and PNG and the diagnosis reports in `backend.spec.ts`, drafts in
 * `drafts-backend.spec.ts`.
 *
 * Runs only when TABRION_STUDIO_E2E_PYTHON names a Python in which Studio is installed with its built frontend, like
 * e2e/backend.spec.ts (CI sets it in the jobs `e2e`, `e2e (firefox)` and `e2e (webkit)`).
 */
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { test, expect, type Page } from '@playwright/test';

import {
  expectNoViolations,
  foreignRequests,
  recordViolations,
  seriousViolations,
  watchSelects,
} from './support';

const python = process.env.TABRION_STUDIO_E2E_PYTHON;
test.skip(!python, 'set TABRION_STUDIO_E2E_PYTHON to run against a real tabrion-studio');
test.describe.configure({ mode: 'serial' });

const NO_KEYRING_ENV = { ...process.env, PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring' };
const LINK = /do not share\): (http:\/\/127\.0\.0\.1:\d+\/launch#\S+)/;

type Catalog = { [key: string]: string | Catalog };
type Language = 'en' | 'de';

function flatten(tree: Catalog, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === 'string') out[path] = value;
    else Object.assign(out, flatten(value, path));
  }
  return out;
}

/** The page's own catalogs, read from the source: the tour finds its controls by the text a user reads. */
const CATALOGS: Record<Language, Record<string, string>> = {
  en: flatten(
    JSON.parse(
      readFileSync(new URL('../src/i18n/locales/en.json', import.meta.url), 'utf8'),
    ) as Catalog,
  ),
  de: flatten(
    JSON.parse(
      readFileSync(new URL('../src/i18n/locales/de.json', import.meta.url), 'utf8'),
    ) as Catalog,
  ),
};

function text(language: Language, key: string): string {
  const value = CATALOGS[language][key];
  if (value === undefined) throw new Error(`no text ${key} in ${language}.json`);
  return value;
}

/**
 * English catalog texts that a German page must not show: every text that differs between the catalogs, has no
 * placeholder and is a phrase (a space and at least 12 characters), so a German page that shows one fell back to
 * English. Names and technical terms are the same in both catalogs and are left out by the comparison.
 */
const ENGLISH_ONLY = Object.entries(CATALOGS.en)
  .filter(
    ([key, value]) =>
      value !== CATALOGS.de[key] &&
      !value.includes('{{') &&
      !value.includes('<') &&
      value.includes(' ') &&
      value.length >= 12,
  )
  .map(([key, value]) => ({ key, value }));
/** Raw keys: a dotted name under one of the catalog's top-level groups (`explorer.tabs.ddl`). */
const KEY_SHAPE = new RegExp(
  `\\b(?:${[...new Set(Object.keys(CATALOGS.en).map((key) => key.split('.')[0]))].join('|')})(?:\\.[A-Za-z_]+)+\\b`,
  'g',
);

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
  root = mkdtempSync(join(tmpdir(), 'studio-e2e-1-0-'));
  mkdirSync(join(root, 'project'));
  database = join(root, 'shop.db');
  // Two related tables with an index, a few rows and a migration version (SQLite's user_version).
  execFileSync(python as string, [
    '-I',
    '-c',
    [
      'import sqlite3, sys',
      'db = sqlite3.connect(sys.argv[1])',
      'db.executescript("""',
      'CREATE TABLE supplier (id INTEGER PRIMARY KEY, name TEXT NOT NULL);',
      'CREATE TABLE parts (id INTEGER PRIMARY KEY, material TEXT NOT NULL,',
      '  supplier_id INTEGER REFERENCES supplier (id));',
      'CREATE INDEX parts_material ON parts (material);',
      "INSERT INTO supplier VALUES (1, 'Acme');",
      "INSERT INTO parts VALUES (1, 'Aluminium', 1), (2, 'Steel', 1), (3, 'Brass', NULL);",
      'PRAGMA user_version = 3;',
      '""")',
      'db.commit()',
      'db.close()',
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

/** Stop the server and wait until it has exited before the folder goes (JR-N2, as in backend.spec.ts). */
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

async function signIn(page: Page): Promise<string[]> {
  const violations = await recordViolations(page);
  await page.goto(links.shift() ?? newLink());
  await expect(page.getByTestId('backend-status')).toBeVisible();
  return violations;
}

interface Answer {
  status: number;
  code: string | null;
  body: unknown;
}

/** One API request from the signed-in page, with the session's CSRF header (as the page sends it). */
async function api(page: Page, method: string, path: string, body?: unknown): Promise<Answer> {
  return page.evaluate(
    async ({ method, path, body }) => {
      const headers: Record<string, string> = {
        'X-Tabrion-CSRF': sessionStorage.getItem('tabrion-studio.csrf') ?? '',
      };
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      const response = await fetch(path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const raw = await response.text();
      let doc: unknown;
      try {
        doc = JSON.parse(raw);
      } catch {
        doc = raw;
      }
      const code =
        doc !== null &&
        typeof doc === 'object' &&
        typeof (doc as { code?: unknown }).code === 'string'
          ? (doc as { code: string }).code
          : null;
      return { status: response.status, code, body: doc };
    },
    { method, path, body },
  );
}

/** What the database holds, read by Python past Studio: every table and index, the rows and the user_version. */
function databaseState(): string {
  return execFileSync(
    python as string,
    [
      '-I',
      '-c',
      [
        'import sqlite3, sys',
        "db = sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True)",
        "print(db.execute('select type, name, sql from sqlite_master order by name').fetchall())",
        "print(db.execute('select * from parts order by id').fetchall())",
        "print(db.execute('select * from supplier order by id').fetchall())",
        "print(db.execute('pragma user_version').fetchone())",
      ].join('\n'),
      database,
    ],
    { encoding: 'utf-8' },
  );
}

function fileDigest(): string {
  return createHash('sha256').update(readFileSync(database)).digest('hex');
}

/**
 * Every text of the page a user can meet, screen-reader text included: the text nodes and the `aria-label`, `title`,
 * `placeholder` and `alt` attributes. Text in an element marked as another language (`lang="en"` on a German page:
 * the English details and remedies that the diagnosis reports from tabrion and Studio's checks, TD-032) is left out,
 * since the page declares it (WCAG 3.1.2); the tests of TD-032 decide when those texts are German.
 */
async function pageTexts(page: Page, language: Language): Promise<string> {
  return page.evaluate((language) => {
    const own = (element: Element | null) =>
      (element?.closest('[lang]')?.getAttribute('lang') ?? language).split('-')[0] === language;
    const parts: string[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
      const parent = node.parentElement;
      if (parent === null || parent.closest('script, style') !== null || !own(parent)) continue;
      parts.push(node.textContent ?? '');
    }
    for (const element of Array.from(
      document.body.querySelectorAll('[aria-label], [title], [placeholder], [alt]'),
    )) {
      if (!own(element)) continue;
      for (const name of ['aria-label', 'title', 'placeholder', 'alt']) {
        const value = element.getAttribute(name);
        if (value) parts.push(value);
      }
    }
    return parts.join('\n');
  }, language);
}

/**
 * axe in light and dark (the theme follows the system by default), and for a German page: no text of the English
 * catalog and no raw key in what the page shows.
 */
async function checkStep(page: Page, language: Language, step: string): Promise<void> {
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await seriousViolations(page), `${step}, ${scheme}`).toEqual([]);
  }
  await page.emulateMedia({ colorScheme: 'light' });
  const shown = await pageTexts(page, language);
  const keys = [...shown.matchAll(KEY_SHAPE)]
    .map((match) => match[0])
    .filter((candidate) => candidate in CATALOGS.en);
  expect(keys, `${step}: raw i18n keys`).toEqual([]);
  if (language === 'de') {
    const english = ENGLISH_ONLY.filter(({ value }) => shown.includes(value)).map(
      ({ key, value }) => `${key}: ${value}`,
    );
    expect(english, `${step}: English texts on the German page`).toEqual([]);
  }
}

async function chooseLanguage(page: Page, language: Language): Promise<void> {
  await page
    .getByRole('link', { name: text('en', 'nav.settings') })
    .or(page.getByRole('link', { name: text('de', 'nav.settings') }))
    .click();
  await page.getByRole('radio', { name: text(language, `languages.${language}`) }).check();
  await expect(
    page.getByRole('heading', { level: 1, name: text(language, 'settings.title') }),
  ).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('lang', language);
}

test('setup: a DEV and a PROD profile on the same SQLite file', async ({ page }) => {
  await signIn(page);
  for (const [name, environment] of [
    ['e2e-shop', 'dev'],
    ['e2e-shop-prod', 'prod'],
  ] as const) {
    // The second write names the file's digest the first one left (the profile file's optimistic lock).
    const listed = (await api(page, 'GET', '/api/profiles')).body as {
      user_file: { digest: string | null } | null;
    };
    const answer = await api(page, 'POST', '/api/profiles', {
      profile: { name, engine: 'sqlite', environment, path: database },
      digest: listed.user_file?.digest ?? null,
      overwrite: false,
    });
    expect(answer.status).toBe(201);
  }
});

for (const language of ['de', 'en'] as const) {
  test(`${language}: the 1.0 tour (DDL, ER diagram, query, Format, export, history, migrations, diagnosis)`, async ({
    page,
  }) => {
    const t = (key: string) => text(language, key);
    const foreign = foreignRequests(page, () => base);
    await watchSelects(page);
    const violations = await signIn(page);
    await chooseLanguage(page, language);
    await checkStep(page, language, 'settings');

    // 08 section 4.3 with STU-022: the DDL of a table, read from the real database.
    await page.getByRole('link', { name: t('nav.explorer') }).click();
    await page.getByLabel(t('explorer.profile')).selectOption({ label: 'e2e-shop (DEV)' });
    await page.getByTestId('explorer-open').click();
    await expect(page.getByTestId('explorer-workspace')).toBeVisible();
    await page.getByRole('treeitem', { name: 'parts', exact: true }).click();
    await page.getByRole('tab', { name: t('explorer.tabs.ddl') }).click();
    await expect(page.getByTestId('ddl-text')).toContainText('CREATE TABLE parts');
    await expect(page.getByTestId('ddl-text')).toContainText('CREATE INDEX parts_material');
    await checkStep(page, language, 'explorer DDL');

    // 08 section 4.7 with STU-023: the ER diagram of the schema and its table alternative.
    await page.getByTestId('explorer-view-diagram').click();
    await expect(page.getByTestId('er-box')).toHaveCount(2);
    await expect(page.getByTestId('er-list-row')).not.toHaveCount(0);
    await checkStep(page, language, 'ER diagram');

    // 08 section 4.4 with STU-020 and STU-021: a query with a parameter, Format, the export dialog.
    expect((await api(page, 'PUT', '/api/drafts', { tabs: [], active_tab: null })).status).toBe(
      200,
    );
    await page.getByRole('link', { name: t('nav.sql') }).click();
    await expect(page.locator('.cm-content')).toBeVisible();
    await page.getByTestId('editor-profile').selectOption({ label: 'e2e-shop (DEV)' });
    await page.locator('.cm-content').click();
    await page.keyboard.press('Control+A');
    await page.keyboard.press('Delete');
    await page.keyboard.insertText('select id,material from parts where material=:material');
    await page.getByTestId('editor-run').click();
    const value = page.getByTestId('param-panel').getByRole('textbox', { name: ':material' });
    await expect(value).toBeFocused();
    await value.fill('Steel');
    await page.getByTestId('editor-run').click();
    await expect(page.getByRole('grid').getByRole('gridcell', { name: 'Steel' })).toBeVisible();
    await checkStep(page, language, 'SQL editor result');
    await page.locator('.cm-content').click();
    await page.keyboard.press('Alt+Shift+F');
    await expect(page.locator('.cm-line').first()).toHaveText('SELECT');
    await page.getByTestId('export-open').click();
    const exportDialog = page.getByRole('dialog', { name: t('export.title') });
    await expect(exportDialog).toBeVisible();
    await checkStep(page, language, 'export dialog');
    await page.keyboard.press('Escape');
    await expect(exportDialog).toBeHidden();

    // 08 section 2 with STU-020: the history keeps the statement and the parameter's name, never its value.
    await page.keyboard.press('Control+h');
    await expect(page.getByRole('heading', { level: 1, name: t('history.title') })).toBeVisible();
    const entry = page.getByTestId('history-entry').first();
    await expect(entry.getByTestId('history-sql')).toHaveText(
      'select id,material from parts where material=:material',
    );
    await expect(entry.getByTestId('history-params')).toContainText(':material');
    await expect(entry).not.toContainText('Steel');
    await checkStep(page, language, 'history');

    // 08 section 4.8 with STU-024: the migration status of the file (SQLite's user_version).
    await page.getByRole('link', { name: t('nav.migrations') }).click();
    await page.getByLabel(t('migrations.status.profile')).selectOption({ label: 'e2e-shop (DEV)' });
    await page.getByRole('button', { name: t('migrations.status.show') }).click();
    await expect(page.getByTestId('migrations-sqlite')).toContainText('3');
    await checkStep(page, language, 'migrations');

    // 08 section 4.9 with STU-025: a diagnosis with its backup hints and the report buttons. The details and
    // remedies of Studio's STU-DX findings are English until TD-032; they are server texts, not catalog texts.
    await page.getByRole('link', { name: t('nav.diagnostics') }).click();
    await page.getByLabel(t('diagnostics.profile')).selectOption({ label: 'e2e-shop (DEV)' });
    await page.getByRole('button', { name: t('diagnostics.run') }).click();
    await expect(page.getByTestId('diagnostics-result')).toBeVisible();
    await expect(page.getByTestId('diagnostics-backup')).toBeVisible();
    await expect(page.getByTestId('diagnostics-report')).toBeVisible();
    await checkStep(page, language, 'diagnosis');

    expect(foreign).toEqual([]);
    await expectNoViolations(page, violations);
  });
}

test('the page offers no data editing (STU-026 locked); on PROD the chip says "Writing locked" and opens nothing', async ({
  page,
}) => {
  const t = (key: string) => text('en', key);
  const before = fileDigest();
  const violations = await signIn(page);
  await chooseLanguage(page, 'en');
  const editing =
    /\b(edit|insert|add row|delete row|save changes|commit|roll ?back|discard|unlock|write mode)\b/i;
  for (const [name, environment] of [
    ['e2e-shop', 'DEV'],
    ['e2e-shop-prod', 'PROD'],
  ] as const) {
    await page.getByRole('link', { name: t('nav.explorer') }).click();
    await page
      .getByLabel(t('explorer.profile'))
      .selectOption({ label: `${name} (${environment})` });
    await page.getByTestId('explorer-open').click();
    if (environment === 'PROD') {
      const ask = page.getByRole('dialog', { name: t('dialogs.prodConnect.title') });
      await expect(ask).toBeVisible();
      await ask.getByRole('button', { name: t('dialogs.prodConnect.connect') }).click();
    }
    await expect(page.getByTestId('explorer-workspace')).toBeVisible();
    await page.getByRole('treeitem', { name: 'parts', exact: true }).click();
    await page.getByTestId('explorer-show-data').click();
    const grid = page.getByTestId('viewer').getByRole('grid');
    await expect(grid.getByRole('gridcell', { name: 'Aluminium' })).toBeVisible();
    // The data viewer is a reader: no field, editable cell or editing control in the grid or anywhere on the page.
    await expect(
      grid.locator('input, textarea, select, [contenteditable]:not([contenteditable="false"])'),
    ).toHaveCount(0);
    await expect(page.getByRole('button', { name: editing })).toHaveCount(0);
    await expect(page.getByRole('link', { name: editing })).toHaveCount(0);
    await grid.getByRole('gridcell', { name: 'Aluminium' }).dblclick();
    await expect(page.locator('main [contenteditable="true"], main input:focus')).toHaveCount(0);
    // 08 section 5 "Unlock writing": the chip is text, not a control; on PROD it says "Writing locked" with its
    // tooltip, and a click opens no dialog.
    const chip = page.getByTestId('access-mode');
    await expect(chip).toHaveText(
      environment === 'PROD'
        ? new RegExp(`^${t('header.access.locked')}`)
        : new RegExp(`^${t('header.access.readOnly')}`),
    );
    expect(await chip.evaluate((node) => node.tagName)).toBe('SPAN');
    await expect(chip).not.toHaveAttribute('role');
    await expect(chip).not.toHaveAttribute('tabindex');
    await expect(chip).toHaveAttribute(
      'title',
      environment === 'PROD' ? t('header.access.lockedHint') : t('header.access.readOnlyHint'),
    );
    await chip.click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    expect(await seriousViolations(page)).toEqual([]);
  }

  // The SQL editor on PROD: a write is refused before it runs, and nothing offers a write mode.
  expect((await api(page, 'PUT', '/api/drafts', { tabs: [], active_tab: null })).status).toBe(200);
  await page.getByRole('link', { name: t('nav.sql') }).click();
  await expect(page.locator('.cm-content')).toBeVisible();
  await page.getByTestId('editor-profile').selectOption({ label: 'e2e-shop-prod (PROD)' });
  await page.locator('.cm-content').click();
  await page.keyboard.press('Control+A');
  await page.keyboard.press('Delete');
  await page.keyboard.insertText("update parts set material = 'Gold'");
  await page.getByTestId('editor-run').click();
  await expect(page.getByTestId('result-status')).toHaveText(
    /^Studio runs only reading statements/,
  );
  await expect(page.getByRole('button', { name: editing })).toHaveCount(0);
  expect(await seriousViolations(page)).toEqual([]);
  await expectNoViolations(page, violations);
  expect(fileDigest()).toBe(before);
});

/** Statements that would change the database, each refused by Studio's statement check before it runs. */
const WRITES = [
  "update parts set material = 'Gold'",
  "insert into parts (material) values ('Gold')",
  "replace into parts (id, material) values (1, 'Gold')",
  'delete from parts',
  'create table evil (x)',
  'create temp table evil (x)',
  'drop table parts',
  'alter table parts add column evil text',
  'create index evil on parts (supplier_id)',
  'select * into evil from parts',
  'analyze',
  "attach database ':memory:' as other",
  'vacuum',
  'reindex',
  'begin',
  'commit',
];
/**
 * Writes the text check lets through (it is best effort, TD-048): the database itself refuses them, since every
 * Studio connection is read-only in the database (STU-T-010, `tests/security/test_read_only.py`).
 */
const WRITES_PAST_THE_CHECK = [
  'with gone as (select 1) delete from parts',
  'pragma user_version = 7',
  'pragma journal_mode = wal',
];

test('the API refuses every write on DEV and on PROD; the database file stays byte for byte the same', async ({
  page,
}) => {
  const before = { digest: fileDigest(), state: databaseState() };
  await signIn(page);
  for (const [profile, environment] of [
    ['user:e2e-shop', 'dev'],
    ['user:e2e-shop-prod', 'prod'],
  ] as const) {
    const opened = await api(page, 'POST', '/api/connections', {
      profile_id: profile,
      confirmed_environment: environment,
    });
    expect(opened.status, profile).toBe(201);
    const connection = opened.body as { connection_id: string; read_only: boolean };
    // Every connection is read-only; there is no other kind (03 section 5.2: PROD can never write in 1.0).
    expect(connection.read_only).toBe(true);
    const id = connection.connection_id;

    // A write mode does not exist: refused with 403 before anything runs.
    for (const sql of ['select 1', WRITES[0] as string]) {
      const answer = await api(page, 'POST', '/api/queries', {
        connection_id: id,
        sql,
        mode: 'write',
      });
      expect([answer.status, answer.code], `${environment} mode write: ${sql}`).toEqual([
        403,
        'studio.query_write_unsupported',
      ]);
    }
    // Every writing statement, through the query and the export route.
    for (const sql of WRITES) {
      const query = await api(page, 'POST', '/api/queries', { connection_id: id, sql });
      expect(query.status, `${environment} query: ${sql}`).toBe(400);
      expect(query.code, `${environment} query: ${sql}`).toMatch(/^studio\.statement_/);
      const exported = await api(page, 'POST', '/api/exports', {
        connection_id: id,
        format: 'csv',
        sql,
      });
      expect(exported.status, `${environment} export: ${sql}`).toBe(400);
      expect(exported.code, `${environment} export: ${sql}`).toMatch(/^studio\.statement_/);
    }
    for (const sql of WRITES_PAST_THE_CHECK) {
      const started = await api(page, 'POST', '/api/queries', { connection_id: id, sql });
      expect(started.status, `${environment} query: ${sql}`).toBe(202);
      const queryId = (started.body as { query_id: string }).query_id;
      let state: { state: string; problem: { code: string } | null } = {
        state: 'running',
        problem: null,
      };
      await expect
        .poll(async () => {
          state = (await api(page, 'GET', `/api/queries/${queryId}`)).body as typeof state;
          return state.state;
        })
        .toBe('failed');
      expect(state.problem?.code, `${environment} query: ${sql}`).toMatch(/^db\./);
      const exported = await api(page, 'POST', '/api/exports', {
        connection_id: id,
        format: 'csv',
        sql,
      });
      expect(exported.status, `${environment} export: ${sql}`).toBeGreaterThanOrEqual(400);
      expect(exported.code, `${environment} export: ${sql}`).toMatch(/^db\./);
    }
    // The data viewer's routes only read: every changing method is refused.
    for (const path of [
      `/api/connections/${id}/tables/main/parts/rows`,
      `/api/connections/${id}/tables/main/parts/cell`,
      `/api/connections/${id}/rows`,
      `/api/connections/${id}/cell`,
    ]) {
      for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
        const answer = await api(page, method, path, { id: 1, material: 'Gold' });
        expect([answer.status, answer.code], `${environment} ${method} ${path}`).toEqual([
          405,
          'studio.method_not_allowed',
        ]);
      }
    }
    const changeset = await api(page, 'POST', `/api/connections/${id}/changesets`, {
      changes: [{ op: 'update', table: 'parts', key: { id: 1 }, values: { material: 'Gold' } }],
    });
    // 03 section 6 names `POST /connections/{id}/changesets` for the Data Editor (STU-026); it does not exist.
    expect([changeset.status, changeset.code], `${environment} changesets`).toEqual([
      405,
      'studio.method_not_allowed',
    ]);
    // A read still works on the same connection afterwards.
    const read = await api(page, 'POST', '/api/queries', {
      connection_id: id,
      sql: 'select count(*) from parts',
    });
    expect(read.status).toBe(202);
  }
  expect(databaseState()).toBe(before.state);
  expect(fileDigest()).toBe(before.digest);
});

/**
 * The changing API routes (POST, PUT, PATCH, DELETE in the server's OpenAPI document), each with why it never writes
 * to a database: they change Studio's own files and state, open or close read-only connections, or run read-only
 * work. A new changing route fails this test until it is reviewed and added here.
 */
const CHANGING_ROUTES: Record<string, string> = {
  'POST /api/project/trust': "the project trust record in Studio's state folder",
  'POST /api/profiles': "a profile in the user's profiles.toml",
  'PUT /api/profiles': "a profile in the user's profiles.toml",
  'DELETE /api/profiles': "a profile in the user's profiles.toml",
  'PUT /api/settings': "Studio's settings.toml",
  'PUT /api/profiles/{profile_id}/secret': "a password in the system's keyring",
  'DELETE /api/profiles/{profile_id}/secret': "a password in the system's keyring",
  'POST /api/project/code-trust': "the code trust record in Studio's state folder",
  'DELETE /api/project/code-trust': "the code trust record in Studio's state folder",
  'POST /api/connections': 'opens a read-only connection',
  'DELETE /api/connections': 'closes a connection',
  'POST /api/connection-tests': 'opens a read-only connection and closes it again',
  'POST /api/queries': 'runs one read-only statement (a write mode is refused)',
  'POST /api/queries/{query_id}/cancel': 'cancels a running query',
  'POST /api/exports': 'streams a read-only query as a file',
  'POST /api/connections/{connection_id}/diagnostics': 'reads the catalog and settings',
  'POST /api/connections/{connection_id}/diagnostics/cancel': 'cancels a running diagnosis',
  'POST /api/connections/{connection_id}/diagnostics/report': 'the report of a read-only diagnosis',
  'DELETE /api/history': "Studio's state database",
  'PUT /api/favorites': "Studio's state database",
  'PUT /api/scripts/file':
    "a .sql file in the project folder (Studio's own scripts, not a database)",
  'PUT /api/drafts': "Studio's state database",
};

test('every changing API route is a known one that never writes to a database', async ({
  page,
}) => {
  await signIn(page);
  const answer = await api(page, 'GET', '/api/openapi.json');
  expect(answer.status).toBe(200);
  const doc = answer.body as { paths: Record<string, Record<string, unknown>> };
  const changing = Object.entries(doc.paths)
    .flatMap(([path, operations]) =>
      Object.keys(operations)
        .map((method) => method.toUpperCase())
        .filter((method) => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method))
        .map((method) => `${method} ${path.replace(/\{profile_id:path\}/, '{profile_id}')}`),
    )
    .sort();
  expect(changing).toEqual(Object.keys(CHANGING_ROUTES).sort());
});
