/**
 * STU-XDM-5: the Database Manager's profile pairs, import and export. The part lists each read profile with its
 * coupled write and admin profiles and their I-109 j state; an export and an import each need a preview first and
 * the file name typed, and send only ids, paths, names, the typed text and the preview's state (never a profile's
 * fields, a secret or a mode). A profile the server will not import cannot be chosen; a name that exists must be
 * changed. No action button takes the focus on its own. axe finds no serious or critical violation (jsdom: no colour
 * contrast).
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';

import i18n from '../../i18n';
import { parseExportPreview, parseImportPreview } from '../../api/dbmProfiles';
import { DatabaseManagerSection } from './DatabaseManager';
import { ProfilesPart } from './DatabaseManagerProfiles';

const PROFILES = [
  {
    id: 'user:parts',
    source: 'user',
    name: 'parts',
    engine: 'sqlite',
    environment: 'dev',
    path: '/home/u/parts.db',
    password: null,
    secret: { source: null, has_secret: null },
  },
  {
    id: 'user:orders',
    source: 'user',
    name: 'orders',
    engine: 'postgresql',
    environment: 'test',
    host: 'db.example.org',
    database: 'orders',
    password: { source: 'env', target: 'ORDERS_PASSWORD' },
    secret: { source: 'env', has_secret: false },
  },
  {
    id: 'user:books',
    source: 'user',
    name: 'books',
    engine: 'postgresql',
    environment: 'prod',
    host: 'localhost',
    database: 'books',
    password: null,
    secret: { source: null, has_secret: null },
  },
];

const WRITES = [
  {
    id: 'write:orders-admin',
    name: 'orders-admin',
    kind: 'admin',
    read_profile: 'orders',
    read_profile_id: 'user:orders',
    engine: 'postgresql',
    environment: 'test',
    secret: { source: 'env', has_secret: true, confirmed: false, foreign: true },
  },
  {
    id: 'write:parts-admin',
    name: 'parts-admin',
    kind: 'admin',
    read_profile: 'parts',
    read_profile_id: 'user:parts',
    engine: 'sqlite',
    environment: 'dev',
    secret: { source: null, has_secret: null, confirmed: null, foreign: null },
  },
];

const EXPORT_PATH = '/home/u/exchange/out.tabrion-profile.toml';
const IMPORT_PATH = '/home/u/exchange/in.tabrion-profile.toml';

const EXPORT_PREVIEW = {
  path: EXPORT_PATH,
  file_name: 'out.tabrion-profile.toml',
  profiles: [
    {
      id: 'user:parts',
      name: 'parts',
      source: 'user',
      engine: 'sqlite',
      environment: 'dev',
      password: null,
      mode: 'read_only',
    },
    {
      id: 'user:orders',
      name: 'orders',
      source: 'user',
      engine: 'postgresql',
      environment: 'test',
      password: 'env',
      mode: 'read_only',
    },
  ],
  left_out: [
    { name: 'parts-admin', kind: 'admin', read_profile: 'parts' },
    { name: 'orders-admin', kind: 'admin', read_profile: 'orders' },
  ],
  state: 'e'.repeat(64),
};

function imported(name: string, extra: Record<string, unknown> = {}) {
  return {
    name,
    engine: 'postgresql',
    environment: 'test',
    path: null,
    host: 'localhost',
    port: 5434,
    database: name,
    user: 'reader',
    sslmode: 'prefer',
    mode: 'read_only',
    password: { source: 'env', target: 'WH_PASSWORD' },
    importable: true,
    refusals: [],
    not_imported: [],
    name_taken: false,
    ...extra,
  };
}

const IMPORT_PREVIEW = {
  path: IMPORT_PATH,
  file_name: 'in.tabrion-profile.toml',
  state: 'f'.repeat(64),
  profiles: [
    imported('warehouse', { not_imported: ['allow', 'description'] }),
    imported('orders', { name_taken: true, password: { source: 'keyring', target: '' } }),
    imported('writer', {
      mode: 'read_write',
      importable: false,
      password: null,
      refusals: [{ field: 'mode', code: 'import.mode' }],
    }),
    imported('shadow', {
      environment: 'dev',
      importable: false,
      password: null,
      refusals: [{ field: 'environment', code: 'import.prod_target' }],
    }),
  ],
  user_file: { digest: 'd'.repeat(64), hand_written: false },
};

interface Call {
  url: string;
  body: Record<string, unknown> | null;
}

type Answer = { status: number; body: unknown };

function setup(
  answers: {
    exportPreview?: Answer;
    exported?: Answer;
    importPreview?: Answer;
    imported?: Answer;
    areas?: Record<string, boolean>;
  } = {},
  whole = false,
) {
  const calls: Call[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
    calls.push({ url, body });
    const json = (value: unknown, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: {
          'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json',
        },
      });
    const reply = (answer: Answer | undefined, fallback: Answer) => {
      const chosen = answer ?? fallback;
      return json(chosen.body, chosen.status);
    };
    switch (url) {
      case '/api/write/areas':
        return json({
          areas: answers.areas ?? {
            data_editor: false,
            schema_designer: false,
            database_manager: true,
          },
        });
      case '/api/profiles':
        return json({
          profiles: PROFILES,
          project_state: 'none',
          user_file: { digest: 'd'.repeat(64), hand_written: false },
          issues: {},
        });
      case '/api/write/profiles':
        return json({ write_profiles: WRITES, kinds: { write: false, admin: true } });
      case '/api/write/database-manager/backups':
        return json({
          backups: [],
          count: 0,
          total_bytes: 0,
          tools: {
            pg_dump: { ok: false, reason: 'tool.not_configured', version: null },
            pg_restore: { ok: false, reason: 'tool.not_configured', version: null },
          },
        });
      case '/api/connections':
        return json({ connections: [] });
      case '/api/write/unlocks':
        return json({ unlocks: [] });
      case '/api/write/database-manager/profiles/export/preview':
        return reply(answers.exportPreview, { status: 200, body: EXPORT_PREVIEW });
      case '/api/write/database-manager/profiles/export':
        return reply(answers.exported, {
          status: 201,
          body: {
            path: EXPORT_PATH,
            file_name: 'out.tabrion-profile.toml',
            profiles: 2,
            mode: '0600',
            audit_pending: false,
          },
        });
      case '/api/write/database-manager/profiles/import/preview':
        return reply(answers.importPreview, { status: 200, body: IMPORT_PREVIEW });
      case '/api/write/database-manager/profiles/import':
        return reply(answers.imported, {
          status: 201,
          body: {
            imported: ['warehouse', 'orders-copy'],
            file_name: 'in.tabrion-profile.toml',
            digest: 'a'.repeat(64),
            audit_pending: false,
          },
        });
      default:
        return new Response(null, { status: 404 });
    }
  };
  const view = render(
    whole ? <DatabaseManagerSection fetcher={fetcher} /> : <ProfilesPart fetcher={fetcher} />,
  );
  return { calls, view };
}

async function serious(container: HTMLElement): Promise<string[]> {
  const result = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
  return result.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => v.id);
}

function sent(calls: Call[], url: string) {
  return calls.filter((c) => c.url === url).map((c) => c.body);
}

describe('the answers of the profile routes', () => {
  it('parses the previews defensively', () => {
    expect(parseExportPreview(EXPORT_PREVIEW)?.leftOut).toEqual([
      { name: 'parts-admin', kind: 'admin', readProfile: 'parts' },
      { name: 'orders-admin', kind: 'admin', readProfile: 'orders' },
    ]);
    expect(parseExportPreview({ ...EXPORT_PREVIEW, state: 1 })).toBeNull();
    expect(parseExportPreview({ ...EXPORT_PREVIEW, profiles: [{ id: 1 }] })).toBeNull();
    expect(parseExportPreview(null)).toBeNull();
    const preview = parseImportPreview(IMPORT_PREVIEW);
    expect(preview?.profiles.map((p) => [p.name, p.importable, p.nameTaken])).toEqual([
      ['warehouse', true, false],
      ['orders', true, true],
      ['writer', false, false],
      ['shadow', false, false],
    ]);
    expect(preview?.profiles[0]?.password).toEqual({ source: 'env', target: 'WH_PASSWORD' });
    expect(parseImportPreview({ ...IMPORT_PREVIEW, user_file: null })).toBeNull();
    expect(parseImportPreview({ ...IMPORT_PREVIEW, profiles: [{}] })).toBeNull();
  });
});

describe('profile pairs, import and export (STU-XDM-5)', () => {
  afterEach(async () => {
    await i18n.changeLanguage('en');
  });

  it('is part of the Database Manager, which is hidden while its flag is off', async () => {
    const { calls } = setup(
      { areas: { data_editor: true, schema_designer: true, database_manager: false } },
      true,
    );
    await waitFor(() => expect(calls.map((c) => c.url)).toContain('/api/write/areas'));
    expect(screen.queryByTestId('dbm-profiles')).toBeNull();
    expect(calls.some((c) => c.url.includes('/profiles/'))).toBe(false);
  });

  it('shows the part inside the Database Manager while its flag is on', async () => {
    const { view } = setup({}, true);
    expect(await screen.findByTestId('dbm-profiles')).toBeVisible();
    expect(await serious(view.container)).toEqual([]);
  });

  it('lists each read profile with its pairs and their confirmation', async () => {
    const { view } = setup();
    const pairs = await screen.findByTestId('dbm-pairs');
    expect(within(pairs).getByTestId('dbm-pair-orders').textContent).toBe(
      'orders (TEST): orders-admin (admin, pair not confirmed yet).',
    );
    expect(within(pairs).getByTestId('dbm-pair-parts').textContent).toBe(
      'parts (DEV): parts-admin (admin, no secret to confirm).',
    );
    expect(within(pairs).getByTestId('dbm-pair-books').textContent).toBe(
      'books (PROD): PROD: never paired; backups only.',
    );
    expect(await serious(view.container)).toEqual([]);
  });

  it('exports after a preview that names the pairs left out, with the file name typed', async () => {
    const user = userEvent.setup();
    const { calls, view } = setup();
    await user.click(await screen.findByTestId('dbm-export-pick-user:orders'));
    await user.click(screen.getByTestId('dbm-export-pick-user:parts'));
    await user.type(screen.getByTestId('dbm-export-path'), EXPORT_PATH);
    expect(screen.queryByTestId('dbm-export-typed')).toBeNull();
    await user.click(screen.getByTestId('dbm-export-preview'));
    const form = await screen.findByTestId('dbm-export-form');
    expect(within(form).getByTestId('dbm-export-left-out').textContent).toBe(
      'Not exported (they stay in Studio): parts-admin (admin), orders-admin (admin).',
    );
    expect(within(form).getByText(/orders \(TEST\), read only; password from its/)).toBeVisible();
    const run = screen.getByTestId('dbm-export-run');
    expect(run).toBeDisabled();
    expect(run).not.toHaveFocus();
    await user.type(screen.getByTestId('dbm-export-typed'), 'out.tabrion-profile.tom');
    expect(run).toBeDisabled();
    await user.type(screen.getByTestId('dbm-export-typed'), 'l');
    expect(await serious(view.container)).toEqual([]);
    await user.click(run);
    expect((await screen.findByTestId('dbm-export-done')).textContent).toBe(
      'Exported 2 profiles to out.tabrion-profile.toml, readable and writable only by you.',
    );
    // In the order the page lists them; only ids, the path, the typed name and the preview's state.
    expect(sent(calls, '/api/write/database-manager/profiles/export/preview')).toEqual([
      { profiles: ['user:parts', 'user:orders'], path: EXPORT_PATH },
    ]);
    expect(sent(calls, '/api/write/database-manager/profiles/export')).toEqual([
      {
        profiles: ['user:parts', 'user:orders'],
        path: EXPORT_PATH,
        typed: 'out.tabrion-profile.toml',
        state: 'e'.repeat(64),
      },
    ]);
  });

  it("shows the export preview's refusal and the field it names", async () => {
    const user = userEvent.setup();
    setup({
      exportPreview: {
        status: 400,
        body: {
          code: 'studio.profile_export_invalid',
          status: 400,
          issues: [{ pointer: 'export.path', code: 'export.not_absolute' }],
        },
      },
    });
    await user.click(await screen.findByTestId('dbm-export-pick-user:parts'));
    await user.type(screen.getByTestId('dbm-export-path'), 'out.tabrion-profile.toml');
    await user.click(screen.getByTestId('dbm-export-preview'));
    await waitFor(() =>
      expect(screen.getByTestId('dbm-export-path')).toHaveAttribute('aria-invalid', 'true'),
    );
    expect(screen.getByTestId('dbm-export-problem')).toHaveTextContent('Nothing was written.');
    expect(screen.queryByTestId('dbm-export-form')).toBeNull();
  });

  it('imports the chosen profiles under their names after a preview, file name typed', async () => {
    const user = userEvent.setup();
    const { calls, view } = setup();
    await user.type(await screen.findByTestId('dbm-import-path'), IMPORT_PATH);
    await user.click(screen.getByTestId('dbm-import-preview'));
    const form = await screen.findByTestId('dbm-import-form');
    expect(await serious(view.container)).toEqual([]);
    // What each profile becomes: a read profile, its server and where its password comes from.
    expect(within(form).getByTestId('dbm-import-row-0')).toHaveTextContent(
      'localhost:5434, database warehouse; password from the environment variable WH_PASSWORD',
    );
    expect(within(form).getByTestId('dbm-import-row-0')).toHaveTextContent(
      "Not imported (Studio's profiles do not have them): allow, description.",
    );
    expect(within(form).getByTestId('dbm-import-row-1')).toHaveTextContent(
      'password entered when you connect',
    );
    // Profiles the server will not import cannot be chosen, and say why.
    expect(screen.getByTestId('dbm-import-pick-2')).toBeDisabled();
    expect(screen.getByTestId('dbm-import-refusals-2')).toHaveTextContent(
      'A write or admin profile: Studio imports read profiles only.',
    );
    expect(screen.getByTestId('dbm-import-pick-3')).toBeDisabled();
    expect(screen.getByTestId('dbm-import-refusals-3')).toHaveTextContent(
      'never imports a second label for a PROD database',
    );
    const run = screen.getByTestId('dbm-import-run');
    await user.click(screen.getByTestId('dbm-import-pick-0'));
    await user.click(screen.getByTestId('dbm-import-pick-1'));
    await user.type(screen.getByTestId('dbm-import-typed'), 'in.tabrion-profile.toml');
    // "orders" exists: it must get another name before the import is offered.
    expect(screen.getByTestId('dbm-import-as-1')).toHaveAttribute('aria-invalid', 'true');
    expect(run).toBeDisabled();
    await user.clear(screen.getByTestId('dbm-import-as-1'));
    await user.type(screen.getByTestId('dbm-import-as-1'), 'orders-copy');
    expect(run).toBeEnabled();
    expect(run).not.toHaveFocus();
    expect(await serious(view.container)).toEqual([]);
    await user.click(run);
    expect((await screen.findByTestId('dbm-import-done')).textContent).toContain(
      'Imported 2 read profiles: warehouse, orders-copy.',
    );
    expect(sent(calls, '/api/write/database-manager/profiles/import')).toEqual([
      {
        path: IMPORT_PATH,
        typed: 'in.tabrion-profile.toml',
        state: 'f'.repeat(64),
        profiles: [
          { name: 'warehouse', as: 'warehouse' },
          { name: 'orders', as: 'orders-copy' },
        ],
        digest: 'd'.repeat(64),
        overwrite: false,
      },
    ]);
    // The pairs and the lists are read again after the import.
    await waitFor(() =>
      expect(calls.filter((c) => c.url === '/api/profiles').length).toBeGreaterThan(1),
    );
  });

  it('asks before it rewrites a profiles file written by hand', async () => {
    const user = userEvent.setup();
    const { calls } = setup({
      importPreview: {
        status: 200,
        body: { ...IMPORT_PREVIEW, user_file: { digest: null, hand_written: true } },
      },
    });
    await user.type(await screen.findByTestId('dbm-import-path'), IMPORT_PATH);
    await user.click(screen.getByTestId('dbm-import-preview'));
    await user.click(await screen.findByTestId('dbm-import-pick-0'));
    await user.type(screen.getByTestId('dbm-import-typed'), 'in.tabrion-profile.toml');
    const run = screen.getByTestId('dbm-import-run');
    expect(run).toBeDisabled();
    await user.click(screen.getByTestId('dbm-import-overwrite'));
    await user.click(run);
    await screen.findByTestId('dbm-import-done');
    expect(sent(calls, '/api/write/database-manager/profiles/import')[0]).toMatchObject({
      overwrite: true,
      digest: null,
    });
  });

  it.each([
    ['studio.dbm_profile_import_multi_host', 'never shortens the list to one host'],
    ['studio.dbm_profile_import_folder', 'a folder of your own'],
    ['studio.dbm_profile_import_reserved', 'its own configuration'],
    ['studio.dbm_profile_import_too_large', '256 KiB'],
  ])('shows the refusal %s of a file and offers no import', async (code, text) => {
    const user = userEvent.setup();
    setup({ importPreview: { status: 400, body: { code, status: 400 } } });
    await user.type(await screen.findByTestId('dbm-import-path'), IMPORT_PATH);
    await user.click(screen.getByTestId('dbm-import-preview'));
    expect(await screen.findByTestId('dbm-import-problem')).toHaveTextContent(text);
    expect(screen.queryByTestId('dbm-import-form')).toBeNull();
  });

  it('shows the issues of a malformed file and of a refused import by their codes', async () => {
    const user = userEvent.setup();
    setup({
      importPreview: {
        status: 400,
        body: {
          code: 'studio.dbm_profile_import_invalid',
          status: 400,
          issues: [{ pointer: 'import.file', code: 'import.plain_password' }],
        },
      },
    });
    await user.type(await screen.findByTestId('dbm-import-path'), IMPORT_PATH);
    await user.click(screen.getByTestId('dbm-import-preview'));
    const problem = await screen.findByTestId('dbm-import-problem');
    expect(problem).toHaveTextContent('Studio imports nothing of this file.');
    expect(problem).toHaveTextContent('Studio never imports a password.');
  });

  it('keeps the form and says so when a name was taken meanwhile', async () => {
    const user = userEvent.setup();
    setup({
      imported: {
        status: 409,
        body: {
          code: 'studio.dbm_profile_import_exists',
          status: 409,
          issues: [{ pointer: 'import.profiles', code: 'import.name_taken' }],
        },
      },
    });
    await user.type(await screen.findByTestId('dbm-import-path'), IMPORT_PATH);
    await user.click(screen.getByTestId('dbm-import-preview'));
    await user.click(await screen.findByTestId('dbm-import-pick-0'));
    await user.type(screen.getByTestId('dbm-import-typed'), 'in.tabrion-profile.toml');
    await user.click(screen.getByTestId('dbm-import-run'));
    expect(await screen.findByTestId('dbm-import-problem')).toHaveTextContent(
      'Studio never replaces a profile by an imported one',
    );
    expect(screen.getByTestId('dbm-import-form')).toBeVisible();
    expect(screen.queryByTestId('dbm-import-done')).toBeNull();
  });

  it('has the German texts', async () => {
    await i18n.changeLanguage('de');
    const user = userEvent.setup();
    setup();
    expect(await screen.findByText('Profilpaare, Import und Export')).toBeInTheDocument();
    expect((await screen.findByTestId('dbm-pair-orders')).textContent).toBe(
      'orders (TEST): orders-admin (Admin, Paar noch nicht bestätigt).',
    );
    await user.type(screen.getByTestId('dbm-import-path'), IMPORT_PATH);
    await user.click(screen.getByTestId('dbm-import-preview'));
    expect(await screen.findByTestId('dbm-import-refusals-2')).toHaveTextContent(
      'Studio importiert nur Leseprofile.',
    );
  });
});
