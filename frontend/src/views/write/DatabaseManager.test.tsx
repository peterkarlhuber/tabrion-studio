/**
 * STU-XDM-2: the Database Manager's backup and restore section is hidden while its flag is off (also with the other
 * areas' flags on), unlocks with an admin profile (never a write profile), makes a backup, lists the backups with their
 * count and size and says a PostgreSQL backup is not complete, previews a restore (extension count), needs the target's
 * name typed and on PostgreSQL a restore profile, sends only IDs, the name and the typed text, and shows the server's
 * refusals by code and issue. STU-XDM-3: the delete previews first, needs the name typed, offers force only when the
 * server does, never preselected and with its own typed name, and sends no name for SQLite. axe finds no serious or critical violation (jsdom: no colour contrast).
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';

import {
  parseBackup,
  parseDelete,
  parseDeletePreview,
  parseListing,
  parsePreview,
  parseRestore,
  parseRoleListing,
  parseRolePreview,
  parseRoleResult,
} from '../../api/databaseManager';
import i18n from '../../i18n';
import { DatabaseManagerSection } from './DatabaseManager';

const PG = {
  id: 'user:shop-dev',
  source: 'user',
  name: 'shop-dev',
  engine: 'postgresql',
  environment: 'dev',
  host: 'localhost',
  port: 5432,
  database: 'shop',
  user: 'reader',
  sslmode: 'prefer',
  password: { source: 'env', target: 'SHOP_PW' },
  secret: { source: 'env', has_secret: true },
};
const LITE = {
  id: 'user:notes',
  source: 'user',
  name: 'notes',
  engine: 'sqlite',
  environment: 'dev',
  path: '/data/notes.db',
  password: null,
  secret: { source: null, has_secret: false },
};

const PG_BACKUP = {
  file: '20261115T101500Z-0123abcd.dump',
  created: '2026-11-15T10:15:00Z',
  engine: 'postgresql',
  size: 4096,
  source: { database: 'shop', profile: 'user:shop-dev' },
  complete: false,
  fingerprint: 'f'.repeat(64),
  extensions: 2,
  tool: { version: '16.4' },
};
const LITE_BACKUP = {
  file: '20261115T101600Z-89abcdef.sqlite',
  created: '2026-11-15T10:16:00Z',
  engine: 'sqlite',
  size: 8192,
  source: { database: 'notes.db', profile: 'user:notes' },
  complete: true,
  fingerprint: 'e'.repeat(64),
  extensions: null,
  tool: null,
};

const TOOL_OK = { ok: true, reason: null, version: '16.4' };

interface Call {
  method: string;
  url: string;
  body: Record<string, unknown> | null;
}

interface Answers {
  areas?: Record<string, boolean>;
  unlocked?: boolean;
  engine?: 'postgresql' | 'sqlite';
  backup?: { status: number; body: unknown };
  restore?: { status: number; body: unknown };
  deletePreview?: { status: number; body: unknown };
  tools?: Record<string, unknown>;
  roles?: { status: number; body: unknown };
  rolePreview?: { status: number; body: unknown };
  roleApply?: { status: number; body: unknown };
}

const ROLES = {
  environment: 'dev',
  roles: [
    {
      name: 'app',
      display: 'app',
      hidden: false,
      attributes: ['login', 'inherit'],
      own: false,
      connection_limit: 5,
      valid_until: null,
    },
    {
      name: 'shop_admin',
      display: 'shop_admin',
      hidden: false,
      attributes: ['login', 'inherit', 'createrole'],
      own: true,
      connection_limit: null,
      valid_until: null,
    },
    {
      name: 'odd\u200bname',
      display: 'odd<U+200B>name',
      hidden: true,
      attributes: [],
      own: false,
      connection_limit: null,
      valid_until: null,
    },
  ],
  truncated: false,
};

function privileges(role: string) {
  return {
    role,
    attributes: ['login', 'inherit'],
    member_of: [],
    reachable: [],
    dangerous: [],
    broad: [],
    public_definers: [],
    owned_objects: 0,
    default_privileges: 0,
    safe: true,
  };
}

function rolePreview(body: Record<string, unknown> | null) {
  const member = typeof body?.member === 'string' ? body.member : null;
  const role = String(body?.role ?? '');
  return {
    action: body?.action,
    request: {},
    environment: 'dev',
    database: 'shop',
    target: body?.action === 'create' ? null : privileges(role),
    member: member === null ? null : privileges(member),
    k2:
      body?.action === 'grant_membership'
        ? { found: [], checked: ['postgres', 'shop'], unchecked: [], no_connect: 1 }
        : null,
    refusals: [],
    warnings: body?.action === 'revoke_membership' ? ['revoke_grantor'] : [],
    typed: member ?? role,
    state: 'a'.repeat(64),
  };
}

const ON = { data_editor: false, schema_designer: false, database_manager: true };

function setup(answers: Answers = {}) {
  const calls: Call[] = [];
  let unlocked = answers.unlocked ?? false;
  const engine = answers.engine ?? 'postgresql';
  const profile = engine === 'postgresql' ? PG : LITE;
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
    calls.push({ method, url, body });
    const json = (value: unknown, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: {
          'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json',
        },
      });
    if (url === '/api/write/areas') return json({ areas: answers.areas ?? ON });
    if (url === '/api/profiles')
      return json({
        profiles: [profile],
        project_state: 'none',
        user_file: { digest: 'd'.repeat(64), hand_written: false },
        issues: {},
      });
    if (url === '/api/connections')
      return json({
        connections: [{ connection_id: 'c1', profile_id: profile.id, environment: 'dev' }],
      });
    if (url === '/api/write/unlocks' && method === 'GET')
      return json({
        unlocks: unlocked
          ? [{ connection_id: 'c1', write_profile_id: 'write:shop-admin', environment: 'dev' }]
          : [],
      });
    if (url === '/api/write/unlocks' && method === 'POST') {
      unlocked = true;
      return json({ connection_id: 'c1' });
    }
    if (url === '/api/write/profiles')
      return json({
        write_profiles: [
          {
            id: 'write:shop-w',
            name: 'shop-w',
            kind: 'write',
            engine,
            read_profile_id: profile.id,
          },
          {
            id: 'write:shop-admin',
            name: 'shop-admin',
            kind: 'admin',
            engine,
            read_profile_id: profile.id,
          },
          {
            id: 'write:shop-restore',
            name: 'shop-restore',
            kind: 'admin',
            engine,
            read_profile_id: profile.id,
          },
        ],
      });
    if (url === '/api/write/database-manager/backups')
      return json({
        backups: [PG_BACKUP, LITE_BACKUP],
        count: 2,
        total_bytes: 12288,
        tools: answers.tools ?? { pg_dump: TOOL_OK, pg_restore: TOOL_OK },
      });
    if (url === '/api/write/database-manager/backup') {
      const answer = answers.backup ?? {
        status: 201,
        body: { backup: engine === 'postgresql' ? PG_BACKUP : LITE_BACKUP, audit_pending: false },
      };
      return json(answer.body, answer.status);
    }
    if (url === '/api/write/database-manager/restore/preview')
      return json(
        engine === 'postgresql'
          ? {
              backup: PG_BACKUP,
              environment: 'dev',
              targets: ['new'],
              extensions: 2,
              restore_profile_required: true,
            }
          : {
              backup: LITE_BACKUP,
              environment: 'dev',
              targets: ['new', 'existing'],
              existing_name: 'notes.db',
            },
      );
    if (url === '/api/write/database-manager/restore') {
      const answer = answers.restore ?? {
        status: 201,
        body: {
          target: body?.target === 'existing' ? 'notes.db' : body?.name,
          verified: true,
          pre_backup: body?.target === 'existing' ? LITE_BACKUP.file : null,
          connections_closed: 0,
          audit_pending: false,
        },
      };
      return json(answer.body, answer.status);
    }
    if (url === '/api/write/database-manager/delete/preview') {
      const answer = answers.deletePreview ?? {
        status: 200,
        body:
          engine === 'postgresql'
            ? {
                engine,
                target: body?.name,
                environment: 'dev',
                sessions: 2,
                force_offered: true,
                backup_required: true,
              }
            : {
                engine,
                target: 'notes.db',
                environment: 'dev',
                sessions: null,
                force_offered: false,
                backup_required: true,
              },
      };
      return json(answer.body, answer.status);
    }
    if (url === '/api/write/database-manager/roles') {
      const answer = answers.roles ?? { status: 200, body: ROLES };
      return json(answer.body, answer.status);
    }
    if (url === '/api/write/database-manager/roles/preview') {
      const answer = answers.rolePreview ?? { status: 200, body: rolePreview(body) };
      return json(answer.body, answer.status);
    }
    if (url === '/api/write/database-manager/roles/apply') {
      const answer = answers.roleApply ?? {
        status: 200,
        body: {
          action: body?.action,
          role: body?.role,
          member: body?.member ?? null,
          after: body?.action === 'drop' ? null : privileges(String(body?.role)),
          inside: null,
          differences: [],
          audit_pending: false,
        },
      };
      return json(answer.body, answer.status);
    }
    if (url === '/api/write/database-manager/delete')
      return json({
        engine,
        target: body?.name ?? 'notes.db',
        pre_backup: engine === 'postgresql' ? PG_BACKUP.file : LITE_BACKUP.file,
        force: body?.force === true,
        connections_closed: 0,
        audit_pending: false,
      });
    return new Response(null, { status: 404 });
  };
  const view = render(<DatabaseManagerSection fetcher={fetcher} />);
  return { calls, view };
}

async function serious(container: HTMLElement): Promise<string[]> {
  const result = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
  return result.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => v.id);
}

const writes = (calls: Call[]) => calls.filter((call) => call.method !== 'GET');

describe('the answers of the Database Manager routes', () => {
  it('parses backups, the listing, the preview and the restore defensively', () => {
    expect(parseBackup(PG_BACKUP)).toMatchObject({
      file: PG_BACKUP.file,
      engine: 'postgresql',
      database: 'shop',
      complete: false,
      extensions: 2,
      toolVersion: '16.4',
    });
    expect(parseBackup({ ...PG_BACKUP, engine: 'oracle' })).toBeNull();
    expect(parseBackup({ ...PG_BACKUP, size: -1 })).toBeNull();
    expect(parseListing({ backups: [PG_BACKUP, { file: 1 }], count: 2 })?.backups).toHaveLength(1);
    expect(parseListing({})).toBeNull();
    expect(
      parsePreview({ backup: { file: 'a' }, targets: ['new', 'drop'], extensions: 'x' }),
    ).toEqual({
      backup: 'a',
      targets: ['new'],
      existingName: null,
      extensions: null,
      restoreProfileRequired: false,
    });
    expect(parseRestore({ target: 'x' })).toBeNull();
    expect(parseRestore({ target: 'x', verified: false, connections_closed: 2 })).toMatchObject({
      verified: false,
      connectionsClosed: 2,
    });
  });
});

describe('the answers of the delete routes', () => {
  it('parses the delete preview and result defensively', () => {
    expect(
      parseDeletePreview({ engine: 'postgresql', target: 'old', sessions: 1, force_offered: true }),
    ).toEqual({
      engine: 'postgresql',
      target: 'old',
      environment: null,
      sessions: 1,
      forceOffered: true,
    });
    expect(parseDeletePreview({ engine: 'mysql', target: 'old' })).toBeNull();
    expect(
      parseDeletePreview({ engine: 'sqlite', target: 'a.db', force_offered: 'yes' })?.forceOffered,
    ).toBe(false);
    expect(parseDelete({ target: 'old' })).toBeNull();
    expect(parseDelete({ target: 'old', pre_backup: 'b.dump', force: 1 })?.force).toBe(false);
  });
});

describe('the Database Manager section', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en');
  });

  it('is hidden while its flag is off, also with the other areas on', async () => {
    const { view, calls } = setup({
      areas: { data_editor: true, schema_designer: true, database_manager: false },
    });
    await waitFor(() => expect(calls.some((call) => call.url === '/api/write/areas')).toBe(true));
    expect(view.container).toBeEmptyDOMElement();
    expect(calls.map((call) => call.url)).toEqual(['/api/write/areas']);
  });

  it('lists the backups of the engine with count, size, tools and the scope notice', async () => {
    const { view } = setup();
    expect(await screen.findByText(/2 backups, 12 KB in all/)).toBeInTheDocument();
    expect(screen.getByText(/Studio never deletes backups/)).toBeInTheDocument();
    expect(screen.getByText(/A PostgreSQL backup is not a complete backup/)).toBeInTheDocument();
    expect(screen.getByTestId('database-manager-tool-pg_dump')).toHaveTextContent(
      'pg_dump 16.4: usable.',
    );
    const list = await screen.findByTestId('database-manager-backups');
    expect(list).toHaveTextContent(PG_BACKUP.file);
    expect(list).not.toHaveTextContent(LITE_BACKUP.file);
    expect(list).toHaveTextContent('not complete');
    expect(await serious(view.container)).toEqual([]);
  });

  it('says why a tool is not usable, without a path', async () => {
    setup({
      tools: {
        pg_dump: { ok: false, reason: 'tool.too_old', version: null },
        pg_restore: { ok: false, reason: 'tool.not_configured', version: null },
      },
    });
    expect(await screen.findByTestId('database-manager-tool-pg_dump')).toHaveTextContent(
      'pg_dump: not usable, the version is too old',
    );
    expect(screen.getByTestId('database-manager-tool-pg_restore')).toHaveTextContent(
      'not set in database-tools.toml',
    );
  });

  it('unlocks with an admin profile only, then makes a backup', async () => {
    const user = userEvent.setup();
    const { calls, view } = setup();
    const unlock = await screen.findByTestId('data-editor-unlock');
    const select = unlock.querySelector('select');
    expect(select).not.toBeNull();
    const options = Array.from(select?.querySelectorAll('option') ?? []).map((o) => o.value);
    expect(options).toEqual(['write:shop-admin', 'write:shop-restore']);
    await user.type(screen.getByLabelText(/Type shop-admin/), 'shop-admin');
    await user.click(screen.getByRole('button', { name: 'Unlock' }));
    const backup = await screen.findByTestId('database-manager-backup');
    expect(await serious(view.container)).toEqual([]);
    await user.click(backup);
    expect(await screen.findByTestId('database-manager-made')).toHaveTextContent(PG_BACKUP.file);
    const sent = writes(calls).find((call) => call.url.endsWith('/backup'));
    expect(sent?.body).toEqual({ connection_id: 'c1' });
  });

  it('restores a PostgreSQL backup into a new database as a restore profile, name typed', async () => {
    const user = userEvent.setup();
    const { calls, view } = setup({ unlocked: true });
    await user.click(await screen.findByTestId('database-manager-preview'));
    expect(await screen.findByTestId('database-manager-extensions')).toHaveTextContent(
      'The archive creates 2 extensions.',
    );
    expect(screen.getByText(/only into a new database/)).toBeInTheDocument();
    const run = screen.getByRole('button', { name: 'Restore' });
    expect(run).toBeDisabled();
    expect(document.activeElement).not.toBe(run);
    await user.type(screen.getByLabelText('New database name'), 'shop_copy');
    expect(screen.getByLabelText(/Restore profile/)).toHaveValue('write:shop-admin');
    await user.selectOptions(screen.getByLabelText(/Restore profile/), 'write:shop-restore');
    await user.type(screen.getByLabelText('Type shop_copy to restore'), 'shop_cop');
    expect(run).toBeDisabled();
    await user.type(screen.getByLabelText('Type shop_copy to restore'), 'y');
    expect(await serious(view.container)).toEqual([]);
    await user.click(run);
    expect(await screen.findByTestId('database-manager-restored')).toHaveTextContent(
      'Restored into shop_copy; its schema matches the backup.',
    );
    const sent = writes(calls).find((call) => call.url.endsWith('/restore'));
    expect(sent?.body).toEqual({
      connection_id: 'c1',
      backup_id: PG_BACKUP.file,
      target: 'new',
      name: 'shop_copy',
      typed: 'shop_copy',
      restore_profile_id: 'write:shop-restore',
    });
  });

  it('restores a SQLite backup over the connection file after a backup of it', async () => {
    const user = userEvent.setup();
    const { calls } = setup({ unlocked: true, engine: 'sqlite' });
    await user.click(await screen.findByTestId('database-manager-preview'));
    await user.click(await screen.findByLabelText(/This connection's file notes.db/));
    expect(screen.getByText(/The file is backed up first, then replaced/)).toBeInTheDocument();
    await user.type(screen.getByLabelText('Type notes.db to restore'), 'notes.db');
    await user.click(screen.getByRole('button', { name: 'Restore' }));
    expect(await screen.findByTestId('database-manager-restored')).toHaveTextContent(
      `The target was backed up first as ${LITE_BACKUP.file}.`,
    );
    const sent = writes(calls).find((call) => call.url.endsWith('/restore'));
    expect(sent?.body).toEqual({
      connection_id: 'c1',
      backup_id: LITE_BACKUP.file,
      target: 'existing',
      typed: 'notes.db',
    });
  });

  it("shows the server's refusal and its issues by code", async () => {
    const user = userEvent.setup();
    setup({
      unlocked: true,
      backup: {
        status: 400,
        body: {
          type: 'about:blank',
          title: 'x',
          status: 400,
          code: 'studio.backup_tool_unavailable',
          issues: [{ pointer: 'tool.pg_dump', code: 'tool.link' }],
        },
      },
    });
    await user.click(await screen.findByTestId('database-manager-backup'));
    const problem = await screen.findByTestId('database-manager-problem');
    expect(problem).toHaveTextContent('pg_dump or pg_restore is not usable');
    expect(problem).toHaveTextContent('the path is a link');
  });

  it('shows the unlock again after the admin role was found over-privileged', async () => {
    const user = userEvent.setup();
    setup({
      unlocked: true,
      backup: {
        status: 405,
        body: {
          type: 'about:blank',
          title: 'x',
          status: 405,
          code: 'studio.admin_over_privileged',
        },
      },
    });
    await user.click(await screen.findByTestId('database-manager-backup'));
    expect(await screen.findByTestId('data-editor-unlock')).toBeInTheDocument();
    expect(screen.getByTestId('database-manager-problem')).toHaveTextContent(
      'The admin role has or reaches SUPERUSER',
    );
  });

  it('deletes a PostgreSQL database by its name, force only with its own typed confirmation', async () => {
    const user = userEvent.setup();
    const { calls, view } = setup({ unlocked: true });
    await user.type(await screen.findByTestId('database-manager-delete-name'), 'shop_old');
    await user.click(screen.getByTestId('database-manager-delete-preview'));
    expect(await screen.findByTestId('database-manager-delete-sessions')).toHaveTextContent(
      '2 other sessions use this database now.',
    );
    expect(writes(calls).filter((call) => call.url.endsWith('/delete'))).toEqual([]);
    const run = screen.getByTestId('database-manager-delete-run');
    expect(run).toBeDisabled();
    expect(document.activeElement).not.toBe(run);
    const force = screen.getByTestId('database-manager-delete-force');
    expect(force).not.toBeChecked();
    await user.type(screen.getByLabelText('Type shop_old to delete'), 'shop_old');
    expect(run).toBeEnabled();
    await user.click(force);
    expect(run).toBeDisabled();
    await user.type(screen.getByTestId('database-manager-delete-force-typed'), 'shop_old');
    expect(run).toBeEnabled();
    expect(await serious(view.container)).toEqual([]);
    await user.click(run);
    expect(await screen.findByTestId('database-manager-deleted')).toHaveTextContent(
      `shop_old was deleted. Its backup from before the delete is ${PG_BACKUP.file}.`,
    );
    const sent = writes(calls).find((call) => call.url.endsWith('/delete'));
    expect(sent?.body).toEqual({
      connection_id: 'c1',
      name: 'shop_old',
      typed: 'shop_old',
      force: true,
      force_typed: 'shop_old',
    });
  });

  it("deletes the SQLite connection's own file without a name or force", async () => {
    const user = userEvent.setup();
    const { calls } = setup({ unlocked: true, engine: 'sqlite' });
    await user.click(await screen.findByTestId('database-manager-delete-preview'));
    expect(await screen.findByTestId('database-manager-delete-warning')).toHaveTextContent(
      'The file notes.db and its journal files are deleted for good.',
    );
    expect(screen.queryByTestId('database-manager-delete-name')).toBeNull();
    expect(screen.queryByTestId('database-manager-delete-force')).toBeNull();
    await user.type(screen.getByLabelText('Type notes.db to delete'), 'notes.db');
    await user.click(screen.getByTestId('database-manager-delete-run'));
    await screen.findByTestId('database-manager-deleted');
    const preview = writes(calls).find((call) => call.url.endsWith('/delete/preview'));
    expect(preview?.body).toEqual({ connection_id: 'c1' });
    const sent = writes(calls).find((call) => call.url.endsWith('/delete'));
    expect(sent?.body).toEqual({ connection_id: 'c1', typed: 'notes.db', force: false });
  });

  it('shows why a delete is refused', async () => {
    const user = userEvent.setup();
    setup({
      unlocked: true,
      deletePreview: {
        status: 400,
        body: {
          type: 'about:blank',
          title: 'x',
          status: 400,
          code: 'studio.dbm_delete_not_allowed',
          issues: [{ pointer: 'delete.target', code: 'delete.connected' }],
        },
      },
    });
    await user.type(await screen.findByTestId('database-manager-delete-name'), 'shop');
    await user.click(screen.getByTestId('database-manager-delete-preview'));
    const problem = await screen.findByTestId('database-manager-problem');
    expect(problem).toHaveTextContent('Studio deletes only a DEV or TEST database');
    expect(problem).toHaveTextContent('the database this connection uses is never deleted');
    expect(screen.queryByTestId('database-manager-delete')).toBeNull();
  });

  it('has the German texts', async () => {
    await i18n.changeLanguage('de');
    setup();
    expect(await screen.findByText(/Datenbank-Manager/)).toBeInTheDocument();
    expect(await screen.findByText(/2 Sicherungen, insgesamt/)).toBeInTheDocument();
    await i18n.changeLanguage('en');
  });
});

describe('the answers of the role routes', () => {
  it('parses the listing, the preview and the result defensively', () => {
    const listing = parseRoleListing(ROLES);
    expect(listing?.roles.map((role) => role.display)).toEqual([
      'app',
      'shop_admin',
      'odd<U+200B>name',
    ]);
    expect(listing?.roles[0]?.connectionLimit).toBe(5);
    expect(parseRoleListing({ roles: 'x' })).toBeNull();
    expect(parseRoleListing({ roles: [{ name: 1 }] })?.roles).toEqual([]);
    const preview = parseRolePreview(
      rolePreview({ action: 'grant_membership', role: 'g', member: 'm' }),
    );
    expect(preview?.typed).toBe('m');
    expect(preview?.k2?.noConnect).toBe(1);
    expect(parseRolePreview({ action: 'superuser', typed: 'x', state: 'y' })).toBeNull();
    expect(parseRoleResult({ action: 'drop', role: 'app', after: null })?.after).toBeNull();
    expect(parseRoleResult({ action: 'drop' })).toBeNull();
  });
});

describe('the role management of the Database Manager (STU-XDM-4)', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en');
  });

  it("lists the roles with Studio's own and hidden names marked, only on PostgreSQL", async () => {
    const { view } = setup({ unlocked: true });
    const list = await screen.findByTestId('database-manager-roles-list');
    expect(list).toHaveTextContent(
      'shop_admin (login, inherit, createrole) — used by Studio, never changed',
    );
    expect(list).toHaveTextContent('odd<U+200B>name (no attributes) — its name holds characters');
    expect(await serious(view.container)).toEqual([]);
  });

  it('has no roles part on SQLite', async () => {
    const { calls } = setup({ unlocked: true, engine: 'sqlite' });
    await screen.findByTestId('database-manager-delete-part');
    expect(screen.queryByTestId('database-manager-roles')).toBeNull();
    expect(calls.some((call) => call.url.includes('/roles'))).toBe(false);
  });

  it('creates a role after a preview and the typed name; the password goes only with the apply', async () => {
    const user = userEvent.setup();
    const { calls, view } = setup({ unlocked: true });
    await user.type(await screen.findByTestId('database-manager-roles-role'), 'reporting');
    await user.click(screen.getByTestId('database-manager-roles-login'));
    await user.click(screen.getByTestId('database-manager-roles-preview'));
    const apply = await screen.findByTestId('database-manager-roles-apply');
    expect(apply).toBeDisabled();
    expect(document.activeElement).not.toBe(apply);
    await user.type(screen.getByTestId('database-manager-roles-password'), 'pw-canary');
    await user.type(screen.getByLabelText('Type reporting to confirm'), 'reporting');
    expect(apply).toBeEnabled();
    expect(await serious(view.container)).toEqual([]);
    await user.click(apply);
    expect(await screen.findByTestId('database-manager-roles-done')).toHaveTextContent(
      'The role reporting was created.',
    );
    const preview = writes(calls).find((call) => call.url.endsWith('/roles/preview'));
    expect(preview?.body).toEqual({
      connection_id: 'c1',
      action: 'create',
      role: 'reporting',
      options: {
        login: true,
        inherit: true,
        create_db: false,
        connection_limit: null,
        valid_until: null,
      },
    });
    const sent = writes(calls).find((call) => call.url.endsWith('/roles/apply'));
    expect(sent?.body).toEqual({
      ...preview?.body,
      state: 'a'.repeat(64),
      typed: 'reporting',
      password: 'pw-canary',
    });
  });

  it("starts an alter from the role's current options", async () => {
    const user = userEvent.setup();
    const { calls } = setup({ unlocked: true });
    await user.selectOptions(await screen.findByTestId('database-manager-roles-action'), 'alter');
    await user.type(screen.getByTestId('database-manager-roles-role'), 'app');
    expect(screen.getByTestId('database-manager-roles-login')).toBeChecked();
    expect(screen.getByTestId('database-manager-roles-limit')).toHaveValue('5');
    await user.click(screen.getByTestId('database-manager-roles-preview'));
    await screen.findByTestId('database-manager-roles-confirm');
    const preview = writes(calls).find((call) => call.url.endsWith('/roles/preview'));
    expect(preview?.body?.options).toEqual({
      login: true,
      inherit: true,
      create_db: false,
      connection_limit: 5,
      valid_until: null,
    });
  });

  it('removes a member with the member typed, as a danger action with its warning', async () => {
    const user = userEvent.setup();
    const { calls } = setup({ unlocked: true });
    await user.selectOptions(
      await screen.findByTestId('database-manager-roles-action'),
      'revoke_membership',
    );
    await user.type(screen.getByTestId('database-manager-roles-role'), 'readers');
    await user.type(screen.getByTestId('database-manager-roles-member'), 'app');
    await user.click(screen.getByTestId('database-manager-roles-preview'));
    expect(await screen.findByTestId('database-manager-roles-warnings')).toHaveTextContent(
      'A revoke removes only what the admin role granted',
    );
    expect(screen.queryByTestId('database-manager-roles-password')).toBeNull();
    await user.type(screen.getByLabelText('Type app to confirm'), 'app');
    await user.click(screen.getByTestId('database-manager-roles-apply'));
    expect(await screen.findByTestId('database-manager-roles-done')).toHaveTextContent(
      'app is no longer a member of readers.',
    );
    const sent = writes(calls).find((call) => call.url.endsWith('/roles/apply'));
    expect(sent?.body).toEqual({
      connection_id: 'c1',
      action: 'revoke_membership',
      role: 'readers',
      member: 'app',
      state: 'a'.repeat(64),
      typed: 'app',
    });
  });

  it('offers TRIGGER only for a revoke', async () => {
    const user = userEvent.setup();
    setup({ unlocked: true });
    await user.selectOptions(
      await screen.findByTestId('database-manager-roles-action'),
      'grant_privileges',
    );
    expect(screen.getByTestId('database-manager-roles-privilege-SELECT')).toBeInTheDocument();
    expect(screen.queryByTestId('database-manager-roles-privilege-TRIGGER')).toBeNull();
    await user.selectOptions(
      screen.getByTestId('database-manager-roles-action'),
      'revoke_privileges',
    );
    expect(screen.getByTestId('database-manager-roles-privilege-TRIGGER')).toBeInTheDocument();
  });

  it('shows the refusals of the preview and offers no apply', async () => {
    const user = userEvent.setup();
    setup({
      unlocked: true,
      rolePreview: {
        status: 200,
        body: {
          ...rolePreview({
            action: 'grant_membership',
            role: 'pg_read_server_files',
            member: 'app',
          }),
          refusals: ['escalation_grant', 'privileged_role'],
          warnings: ['escalation_check_partial'],
        },
      },
    });
    await user.selectOptions(
      await screen.findByTestId('database-manager-roles-action'),
      'grant_membership',
    );
    await user.type(screen.getByTestId('database-manager-roles-role'), 'pg_read_server_files');
    await user.type(screen.getByTestId('database-manager-roles-member'), 'app');
    await user.click(screen.getByTestId('database-manager-roles-preview'));
    const refusals = await screen.findByTestId('database-manager-roles-refusals');
    expect(refusals).toHaveTextContent('The group is a predefined or server-file role');
    expect(refusals).toHaveTextContent('The group holds TRIGGER');
    expect(screen.getByTestId('database-manager-roles-k2')).toHaveTextContent(
      'checked in: postgres, shop',
    );
    expect(screen.queryByTestId('database-manager-roles-apply')).toBeNull();
  });

  it("shows the server's refusal of a role change by code and issue", async () => {
    const user = userEvent.setup();
    setup({
      unlocked: true,
      rolePreview: {
        status: 405,
        body: {
          type: 'about:blank',
          title: 'x',
          status: 405,
          code: 'studio.dbm_role_not_allowed',
          issues: [{ pointer: 'role.target', code: 'role.prod' }],
        },
      },
    });
    await user.type(await screen.findByTestId('database-manager-roles-role'), 'x');
    await user.click(screen.getByTestId('database-manager-roles-preview'));
    const problem = await screen.findByTestId('database-manager-roles-problem');
    expect(problem).toHaveTextContent('never on a server that a PROD profile names');
    expect(problem).toHaveTextContent('A PROD profile names this server');
  });

  it('shows the unlock again when the admin role is over-privileged', async () => {
    setup({
      unlocked: true,
      roles: {
        status: 405,
        body: {
          type: 'about:blank',
          title: 'x',
          status: 405,
          code: 'studio.admin_over_privileged',
        },
      },
    });
    expect(await screen.findByTestId('data-editor-unlock')).toBeInTheDocument();
    expect(screen.queryByTestId('database-manager-roles')).toBeNull();
  });

  it('has the German texts', async () => {
    await i18n.changeLanguage('de');
    setup({ unlocked: true });
    expect(await screen.findByText('Rollen')).toBeInTheDocument();
    expect(await screen.findByText(/von Studio genutzt, wird nie geändert/)).toBeInTheDocument();
    await i18n.changeLanguage('en');
  });
});
