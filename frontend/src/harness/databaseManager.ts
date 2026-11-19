/**
 * The harness's fake Database Manager server (scenario `database-manager`, STU-XDM-6): the area's dialogs in a browser
 * without a PostgreSQL server or the `pg_dump` tools. A DEV connection `c1` of the read profile `shop-dev`
 * (`engine=postgresql`, the default) or `notes` (`engine=sqlite`) with the admin profile `shop-admin` and the lean
 * restore profile `shop-restore`; the unlock needs the typed name, as on the real server.
 *
 * Answered, as the real routes' shapes (`src/api/databaseManager.ts` parses them): the backup list (two backups, the
 * tools configured), a new backup, the restore preview and run (D-12: PostgreSQL only into a new database, SQLite
 * also over the connection's own file), the delete preview and run (D-13; the backup before the delete, D-11), the
 * role list, preview and apply (D-14) and, while the Schema Designer flag is on too (`runtime=1`, the default for
 * PostgreSQL), the runtime-role wizard. `refuse=role` gives the role preview a refusal and a warning; `locked=1`
 * makes the next backup answer `studio.write_locked`, so the page shows the unlock again.
 *
 * Every request the page sent is kept in `log.calls` (method, path and body) for the e2e spec, which checks that a
 * password goes only with an apply, once. Nothing here is built into the wheel.
 */

export interface DatabaseManagerCall {
  method: string;
  url: string;
  body: Record<string, unknown> | null;
}

export interface DatabaseManagerLog {
  calls: DatabaseManagerCall[];
}

export interface DatabaseManagerOptions {
  engine: 'postgresql' | 'sqlite';
  runtime: boolean;
  refuse: string | null;
  locked: boolean;
}

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
  sslmode: 'verify-full',
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
      name: 'readers',
      display: 'readers',
      hidden: false,
      attributes: ['inherit'],
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

function rolePreview(body: Record<string, unknown> | null, refuse: boolean) {
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
        ? { found: [], checked: ['postgres', 'shop'], unchecked: ['archive'], no_connect: 1 }
        : null,
    refusals: refuse ? ['predefined_role'] : [],
    warnings: refuse || body?.action === 'revoke_membership' ? ['revoke_grantor'] : [],
    typed: member ?? role,
    state: 'a'.repeat(64),
  };
}

function runtimePreview(body: Record<string, unknown> | null) {
  const role = String(body?.role ?? '');
  return {
    action: 'runtime',
    request: {
      role,
      schema: body?.schema ?? 'public',
      protected_tables: body?.protected_tables ?? ['alembic_version'],
      create: body?.create === true,
    },
    environment: 'dev',
    database: 'shop',
    target: body?.create === true ? null : privileges(role),
    k2: null,
    schema: {
      owner_reached: true,
      foreign_owned: 0,
      security_definers: 0,
      tables: 3,
      protected_found: ['alembic_version'],
      protected_missing: [],
    },
    refusals: [],
    warnings: ['rerun_after_migration'],
    typed: role,
    state: 'b'.repeat(64),
  };
}

export function createDatabaseManagerServer(options: DatabaseManagerOptions): {
  fetch: typeof fetch;
  log: DatabaseManagerLog;
} {
  const log: DatabaseManagerLog = { calls: [] };
  const engine = options.engine;
  const profile = engine === 'postgresql' ? PG : LITE;
  let unlocked = false;
  let lockNext = options.locked;
  const backups: unknown[] = [PG_BACKUP, LITE_BACKUP];

  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const body =
      typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : null;
    log.calls.push({ method, url, body });
    const json = (value: unknown, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: {
          'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json',
        },
      });
    const problem = (status: number, code: string, title: string) =>
      json({ type: 'about:blank', status, code, title, detail: title }, status);

    if (url === '/api/write/areas')
      return json({
        areas: { data_editor: false, schema_designer: options.runtime, database_manager: true },
      });
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
      // D-03, as the real server: exactly the admin profile's name.
      if (body?.typed_name !== 'shop-admin')
        return problem(400, 'studio.unlock_name_mismatch', 'Not the name');
      unlocked = true;
      return json({
        connection_id: 'c1',
        write_profile_id: 'write:shop-admin',
        environment: 'dev',
      });
    }
    if (url.startsWith('/api/write/unlocks') && method === 'DELETE') {
      unlocked = false;
      return new Response(null, { status: 204 });
    }
    if (url === '/api/write/profiles')
      return json({
        write_profiles: [
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
        backups,
        count: backups.length,
        total_bytes: backups.length * 4096,
        tools: { pg_dump: TOOL_OK, pg_restore: TOOL_OK },
      });
    if (url === '/api/write/database-manager/backup') {
      if (lockNext) {
        lockNext = false;
        unlocked = false;
        return problem(409, 'studio.write_locked', 'Locked');
      }
      const made = {
        ...(engine === 'postgresql' ? PG_BACKUP : LITE_BACKUP),
        file:
          engine === 'postgresql'
            ? '20261119T090000Z-00c0ffee.dump'
            : '20261119T090000Z-00c0ffee.sqlite',
      };
      backups.unshift(made);
      return json({ backup: made, audit_pending: false }, 201);
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
    if (url === '/api/write/database-manager/restore')
      return json(
        {
          target: body?.target === 'existing' ? 'notes.db' : body?.name,
          verified: true,
          pre_backup: body?.target === 'existing' ? LITE_BACKUP.file : null,
          connections_closed: 0,
          audit_pending: false,
        },
        201,
      );
    if (url === '/api/write/database-manager/delete/preview')
      return json(
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
      );
    if (url === '/api/write/database-manager/delete')
      return json({
        engine,
        target: body?.name ?? 'notes.db',
        pre_backup: engine === 'postgresql' ? PG_BACKUP.file : LITE_BACKUP.file,
        force: body?.force === true,
        connections_closed: 0,
        audit_pending: false,
      });
    if (url === '/api/write/database-manager/roles') return json(ROLES);
    if (url === '/api/write/database-manager/roles/preview')
      return json(rolePreview(body, options.refuse === 'role'));
    if (url === '/api/write/database-manager/roles/apply')
      return json({
        action: body?.action,
        role: body?.role,
        member: body?.member ?? null,
        after: body?.action === 'drop' ? null : privileges(String(body?.role)),
        inside: null,
        differences: [],
        audit_pending: false,
      });
    if (url === '/api/write/database-manager/runtime-role/preview')
      return json(runtimePreview(body));
    if (url === '/api/write/database-manager/runtime-role/apply')
      return json({
        action: 'runtime',
        role: body?.role,
        schema: body?.schema,
        created: body?.create === true,
        connect: true,
        can_create: false,
        tables: 3,
        after: null,
        audit_pending: false,
      });
    return problem(404, 'studio.not_found', 'Not found');
  };
  return { fetch: fetcher, log };
}
