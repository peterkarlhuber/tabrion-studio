/**
 * The Database Manager's backup, restore and delete (STU-XDM-2, STU-XDM-3; STU-ADR-0008, gate G-S4, not granted).
 * The routes answer only while the owner of the Studio process turned the `database_manager` flag on in
 * `write-areas.toml`; the page reads that state (`GET /api/write/areas`) and never sets it. The server decides
 * everything that matters: the tools (only from the owner's `database-tools.toml`, never from the page), the admin
 * unlock, the target (never PROD, on PostgreSQL only a new database for a restore, never a maintenance or the connected
 * database for a delete), the restore role, the checks of the backup and the backup before a delete. The page sends
 * IDs, the target's name and what the user typed; never a path, a tool or an environment.
 */
import { getJson, send, type Result } from './profiles';

export type Engine = 'sqlite' | 'postgresql';

export interface BackupItem {
  file: string;
  created: string;
  engine: Engine;
  size: number;
  /** The database or file name the backup was made of. */
  database: string | null;
  sourceProfile: string | null;
  /** A pg_dump archive is never complete: no global roles, memberships, tablespaces (E-X5). */
  complete: boolean;
  fingerprint: string | null;
  extensions: number | null;
  toolVersion: string | null;
}

export interface ToolState {
  ok: boolean;
  /** `tool.<reason>` when not usable (never a path). */
  reason: string | null;
  version: string | null;
}

export interface BackupListing {
  backups: BackupItem[];
  count: number;
  totalBytes: number;
  tools: { pg_dump: ToolState; pg_restore: ToolState };
}

export interface RestorePreview {
  backup: string;
  targets: ('new' | 'existing')[];
  existingName: string | null;
  extensions: number | null;
  restoreProfileRequired: boolean;
}

export interface RestoreResult {
  target: string;
  verified: boolean;
  preBackup: string | null;
  connectionsClosed: number;
  auditPending: boolean;
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function count(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null;
}

function engine(value: unknown): Engine | null {
  return value === 'sqlite' || value === 'postgresql' ? value : null;
}

export function parseBackup(value: unknown): BackupItem | null {
  const doc = record(value);
  if (doc === null) return null;
  const file = text(doc.file);
  const kind = engine(doc.engine);
  const size = count(doc.size);
  if (file === null || kind === null || size === null) return null;
  const source = record(doc.source);
  const tool = record(doc.tool);
  return {
    file,
    created: text(doc.created) ?? '',
    engine: kind,
    size,
    database: text(source?.database),
    sourceProfile: text(source?.profile),
    complete: doc.complete === true,
    fingerprint: text(doc.fingerprint),
    extensions: count(doc.extensions),
    toolVersion: text(tool?.version),
  };
}

function tool(value: unknown): ToolState {
  const doc = record(value);
  return {
    ok: doc?.ok === true,
    reason: text(doc?.reason),
    version: text(doc?.version),
  };
}

export function parseListing(body: unknown): BackupListing | null {
  const doc = record(body);
  if (doc === null || !Array.isArray(doc.backups)) return null;
  const tools = record(doc.tools);
  return {
    backups: doc.backups.flatMap((item) => {
      const parsed = parseBackup(item);
      return parsed === null ? [] : [parsed];
    }),
    count: count(doc.count) ?? 0,
    totalBytes: count(doc.total_bytes) ?? 0,
    tools: { pg_dump: tool(tools?.pg_dump), pg_restore: tool(tools?.pg_restore) },
  };
}

export function parsePreview(body: unknown): RestorePreview | null {
  const doc = record(body);
  const backup = record(doc?.backup);
  const file = text(backup?.file);
  if (doc === null || file === null || !Array.isArray(doc.targets)) return null;
  return {
    backup: file,
    targets: doc.targets.filter(
      (item): item is 'new' | 'existing' => item === 'new' || item === 'existing',
    ),
    existingName: text(doc.existing_name),
    extensions: count(doc.extensions),
    restoreProfileRequired: doc.restore_profile_required === true,
  };
}

export function parseRestore(body: unknown): RestoreResult | null {
  const doc = record(body);
  const target = text(doc?.target);
  if (doc === null || target === null || typeof doc.verified !== 'boolean') return null;
  return {
    target,
    verified: doc.verified,
    preBackup: text(doc.pre_backup),
    connectionsClosed: count(doc.connections_closed) ?? 0,
    auditPending: doc.audit_pending === true,
  };
}

const UNREADABLE: Result<never> = {
  ok: false,
  problem: { code: 'studio.answer_invalid', kind: 'internal', status: 0, logId: null },
};

/** `GET /api/write/database-manager/backups`: the backups, their count and size, and the tools' state. */
export async function fetchBackups(fetcher: typeof fetch): Promise<BackupListing | null> {
  return parseListing(await getJson(fetcher, '/api/write/database-manager/backups'));
}

/** `POST /api/write/database-manager/backup`: back up the database of this session's unlocked admin connection. */
export async function makeBackup(
  fetcher: typeof fetch,
  connectionId: string,
): Promise<Result<BackupItem>> {
  const result = await send<unknown>(fetcher, 'POST', '/api/write/database-manager/backup', {
    connection_id: connectionId,
  });
  if (!result.ok) return result;
  const backup = parseBackup(record(result.value)?.backup);
  return backup === null ? UNREADABLE : { ok: true, value: backup };
}

/** `POST /api/write/database-manager/restore/preview`: what a restore would do; changes nothing. */
export async function previewRestore(
  fetcher: typeof fetch,
  connectionId: string,
  backupId: string,
): Promise<Result<RestorePreview>> {
  const result = await send<unknown>(
    fetcher,
    'POST',
    '/api/write/database-manager/restore/preview',
    {
      connection_id: connectionId,
      backup_id: backupId,
    },
  );
  if (!result.ok) return result;
  const preview = parsePreview(result.value);
  return preview === null ? UNREADABLE : { ok: true, value: preview };
}

export interface RestoreRequest {
  backupId: string;
  target: 'new' | 'existing';
  /** The new database's or file's name (target `new`). */
  name: string;
  typed: string;
  /** PostgreSQL: the lean restore profile (`write:<name>`). */
  restoreProfileId: string | null;
}

/** `POST /api/write/database-manager/restore`. */
export async function runRestore(
  fetcher: typeof fetch,
  connectionId: string,
  request: RestoreRequest,
): Promise<Result<RestoreResult>> {
  const body: Record<string, string> = {
    connection_id: connectionId,
    backup_id: request.backupId,
    target: request.target,
    typed: request.typed,
  };
  if (request.target === 'new') body.name = request.name;
  if (request.restoreProfileId !== null) body.restore_profile_id = request.restoreProfileId;
  const result = await send<unknown>(fetcher, 'POST', '/api/write/database-manager/restore', body);
  if (!result.ok) return result;
  const restored = parseRestore(result.value);
  return restored === null ? UNREADABLE : { ok: true, value: restored };
}

/** What deleting a database would do (STU-XDM-3): the name to type and, on PostgreSQL, the other sessions. */
export interface DeletePreview {
  engine: Engine;
  /** The database's name (PostgreSQL) or the file's name (SQLite): what the user types to confirm. */
  target: string;
  environment: string | null;
  /** PostgreSQL: the other sessions on the target now (a hint that can change); SQLite: null. */
  sessions: number | null;
  /** Whether `force` may be chosen: only with other sessions, on PostgreSQL 13 or later. */
  forceOffered: boolean;
}

export interface DeleteResult {
  target: string;
  /** The verified backup made right before the delete. */
  preBackup: string;
  force: boolean;
  connectionsClosed: number;
  auditPending: boolean;
}

export function parseDeletePreview(body: unknown): DeletePreview | null {
  const doc = record(body);
  const target = text(doc?.target);
  const kind = engine(doc?.engine);
  if (doc === null || target === null || kind === null) return null;
  return {
    engine: kind,
    target,
    environment: text(doc.environment),
    sessions: count(doc.sessions),
    forceOffered: doc.force_offered === true,
  };
}

export function parseDelete(body: unknown): DeleteResult | null {
  const doc = record(body);
  const target = text(doc?.target);
  const preBackup = text(doc?.pre_backup);
  if (doc === null || target === null || preBackup === null) return null;
  return {
    target,
    preBackup,
    force: doc.force === true,
    connectionsClosed: count(doc.connections_closed) ?? 0,
    auditPending: doc.audit_pending === true,
  };
}

/**
 * `POST /api/write/database-manager/delete/preview`: what deleting would do; changes nothing. `name` is the
 * PostgreSQL database's name; a SQLite delete is always the connection's own file (no name is sent).
 */
export async function previewDelete(
  fetcher: typeof fetch,
  connectionId: string,
  name: string | null,
): Promise<Result<DeletePreview>> {
  const body: Record<string, string> = { connection_id: connectionId };
  if (name !== null) body.name = name;
  const result = await send<unknown>(
    fetcher,
    'POST',
    '/api/write/database-manager/delete/preview',
    body,
  );
  if (!result.ok) return result;
  const preview = parseDeletePreview(result.value);
  return preview === null ? UNREADABLE : { ok: true, value: preview };
}

export interface DeleteRequest {
  /** PostgreSQL: the database's name; SQLite: null. */
  name: string | null;
  typed: string;
  /** PostgreSQL 13 or later with other sessions only: end them (`WITH (FORCE)`). Never a default. */
  force: boolean;
  /** The name typed once more for `force`. */
  forceTyped: string;
}

/** `POST /api/write/database-manager/delete`: a verified backup first, then the delete. */
export async function runDelete(
  fetcher: typeof fetch,
  connectionId: string,
  request: DeleteRequest,
): Promise<Result<DeleteResult>> {
  const body: Record<string, string | boolean> = {
    connection_id: connectionId,
    typed: request.typed,
    force: request.force,
  };
  if (request.name !== null) body.name = request.name;
  if (request.force) body.force_typed = request.forceTyped;
  const result = await send<unknown>(fetcher, 'POST', '/api/write/database-manager/delete', body);
  if (!result.ok) return result;
  const deleted = parseDelete(result.value);
  return deleted === null ? UNREADABLE : { ok: true, value: deleted };
}

// --- Roles (STU-XDM-4) ----------------------------------------------------------------------------------------------

/**
 * PostgreSQL role management through the admin profile (STU-XDM-4; E-X6). Every change runs through Tabrion's role
 * DDL on the server, which never grants SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION; the page sends the action,
 * the names, the options and, for create and alter only, a password straight to the apply route (never to the
 * preview, never stored). Names come back escaped: a character that cannot be seen shows as `<U+XXXX>`.
 */
export type RoleAction =
  | 'create'
  | 'alter'
  | 'drop'
  | 'grant_membership'
  | 'revoke_membership'
  | 'grant_privileges'
  | 'revoke_privileges';

export const ROLE_ACTIONS: RoleAction[] = [
  'create',
  'alter',
  'drop',
  'grant_membership',
  'revoke_membership',
  'grant_privileges',
  'revoke_privileges',
];

/** The changes the page asks to be read with care: they take rights away or remove the role. */
export const DESTRUCTIVE_ROLE_ACTIONS = new Set<RoleAction>([
  'drop',
  'revoke_membership',
  'revoke_privileges',
]);

export type ObjectKind = 'table' | 'sequence' | 'function' | 'schema' | 'database';

export const OBJECT_KINDS: ObjectKind[] = ['table', 'sequence', 'function', 'schema', 'database'];

/** The privileges per object kind. TRIGGER can only be revoked, never granted (K-2). */
export const KIND_PRIVILEGES: Record<ObjectKind, string[]> = {
  table: ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'],
  sequence: ['USAGE', 'SELECT', 'UPDATE'],
  function: ['EXECUTE'],
  schema: ['USAGE', 'CREATE'],
  database: ['CONNECT', 'CREATE'],
};

export interface RoleItem {
  name: string;
  display: string;
  hidden: boolean;
  attributes: string[];
  own: boolean;
  connectionLimit: number | null;
  validUntil: string | null;
}

export interface RoleListing {
  environment: string | null;
  roles: RoleItem[];
  truncated: boolean;
}

export interface RoleOptionsInput {
  login: boolean;
  inherit: boolean;
  create_db: boolean;
  connection_limit: number | null;
  valid_until: string | null;
}

export interface RoleRequest {
  action: RoleAction;
  role: string;
  member?: string;
  options?: RoleOptionsInput;
  on?: ObjectKind;
  privileges?: string[];
  objects?: string[];
}

export interface RolePrivilegesView {
  role: string;
  attributes: string[];
  memberOf: string[];
  dangerous: string[];
  broad: string[];
  publicDefiners: string[];
  /** Objects the role owns in any database of the cluster (default privileges counted apart). */
  ownedObjects: number;
  defaultPrivileges: number;
  safe: boolean;
}

export interface EscalationCheck {
  found: string[];
  checked: string[];
  unchecked: string[];
  noConnect: number;
}

export interface RolePreview {
  action: RoleAction;
  environment: string | null;
  database: string;
  target: RolePrivilegesView | null;
  member: RolePrivilegesView | null;
  k2: EscalationCheck | null;
  refusals: string[];
  warnings: string[];
  typed: string;
  state: string;
}

export interface RoleResult {
  action: RoleAction;
  role: string;
  member: string | null;
  after: RolePrivilegesView | null;
  differences: string[];
  auditPending: boolean;
}

function texts(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

function roleAction(value: unknown): RoleAction | null {
  return ROLE_ACTIONS.find((item) => item === value) ?? null;
}

export function parseRoleListing(body: unknown): RoleListing | null {
  const doc = record(body);
  if (doc === null || !Array.isArray(doc.roles)) return null;
  return {
    environment: text(doc.environment),
    roles: doc.roles.flatMap((value) => {
      const item = record(value);
      const name = text(item?.name);
      const display = text(item?.display);
      if (item === null || name === null || display === null) return [];
      return [
        {
          name,
          display,
          hidden: item.hidden === true,
          attributes: texts(item.attributes),
          own: item.own === true,
          connectionLimit: count(item.connection_limit),
          validUntil: text(item.valid_until),
        },
      ];
    }),
    truncated: doc.truncated === true,
  };
}

export function parsePrivileges(value: unknown): RolePrivilegesView | null {
  const doc = record(value);
  const role = text(doc?.role);
  if (doc === null || role === null) return null;
  return {
    role,
    attributes: texts(doc.attributes),
    memberOf: texts(doc.member_of),
    dangerous: texts(doc.dangerous),
    broad: texts(doc.broad),
    publicDefiners: texts(doc.public_definers),
    ownedObjects: count(doc.owned_objects) ?? 0,
    defaultPrivileges: count(doc.default_privileges) ?? 0,
    safe: doc.safe === true,
  };
}

export function parseRolePreview(body: unknown): RolePreview | null {
  const doc = record(body);
  const action = roleAction(doc?.action);
  const typed = text(doc?.typed);
  const state = text(doc?.state);
  if (doc === null || action === null || typed === null || state === null) return null;
  const k2 = record(doc.k2);
  return {
    action,
    environment: text(doc.environment),
    database: text(doc.database) ?? '',
    target: parsePrivileges(doc.target),
    member: parsePrivileges(doc.member),
    k2:
      k2 === null
        ? null
        : {
            found: texts(k2.found),
            checked: texts(k2.checked),
            unchecked: texts(k2.unchecked),
            noConnect: count(k2.no_connect) ?? 0,
          },
    refusals: texts(doc.refusals),
    warnings: texts(doc.warnings),
    typed,
    state,
  };
}

export function parseRoleResult(body: unknown): RoleResult | null {
  const doc = record(body);
  const action = roleAction(doc?.action);
  const role = text(doc?.role);
  if (doc === null || action === null || role === null) return null;
  return {
    action,
    role,
    member: text(doc.member),
    after: parsePrivileges(doc.after),
    differences: texts(doc.differences),
    auditPending: doc.audit_pending === true,
  };
}

/** `POST /api/write/database-manager/roles`: the server's roles (not the predefined ones), names escaped. */
export async function fetchRoles(
  fetcher: typeof fetch,
  connectionId: string,
): Promise<Result<RoleListing>> {
  const result = await send<unknown>(fetcher, 'POST', '/api/write/database-manager/roles', {
    connection_id: connectionId,
  });
  if (!result.ok) return result;
  const listing = parseRoleListing(result.value);
  return listing === null ? UNREADABLE : { ok: true, value: listing };
}

function roleBody(connectionId: string, request: RoleRequest): Record<string, unknown> {
  const body: Record<string, unknown> = {
    connection_id: connectionId,
    action: request.action,
    role: request.role,
  };
  if (request.member !== undefined) body.member = request.member;
  if (request.options !== undefined) body.options = request.options;
  if (request.on !== undefined) body.on = request.on;
  if (request.privileges !== undefined) body.privileges = request.privileges;
  if (request.objects !== undefined) body.objects = request.objects;
  return body;
}

/** `POST /api/write/database-manager/roles/preview`: what the change would do and what refuses it; changes nothing. */
export async function previewRole(
  fetcher: typeof fetch,
  connectionId: string,
  request: RoleRequest,
): Promise<Result<RolePreview>> {
  const result = await send<unknown>(
    fetcher,
    'POST',
    '/api/write/database-manager/roles/preview',
    roleBody(connectionId, request),
  );
  if (!result.ok) return result;
  const preview = parseRolePreview(result.value);
  return preview === null ? UNREADABLE : { ok: true, value: preview };
}

/**
 * `POST /api/write/database-manager/roles/apply`: the change, bound to the preview's `state` and the typed name.
 * `password` only for create and alter; it goes to the server once and is not kept by the page.
 */
export async function applyRole(
  fetcher: typeof fetch,
  connectionId: string,
  request: RoleRequest,
  confirm: { state: string; typed: string; password: string | null },
): Promise<Result<RoleResult>> {
  const body = roleBody(connectionId, request);
  body.state = confirm.state;
  body.typed = confirm.typed;
  if (confirm.password !== null) body.password = confirm.password;
  const result = await send<unknown>(
    fetcher,
    'POST',
    '/api/write/database-manager/roles/apply',
    body,
  );
  if (!result.ok) return result;
  const done = parseRoleResult(result.value);
  return done === null ? UNREADABLE : { ok: true, value: done };
}

// --- the runtime-role wizard (STU-CR-6 part 2; both the Schema Designer and the Database Manager flag) ------------

/** A runtime-role wizard run: create the role (or take an existing one again) and grant it one schema's rows. */
export interface RuntimeRoleRequest {
  role: string;
  schema: string;
  protectedTables: string[];
  create: boolean;
}

export interface RuntimeSchemaFacts {
  tables: number;
  protectedFound: string[];
  protectedMissing: string[];
}

export interface RuntimeRolePreview {
  environment: string | null;
  database: string;
  target: RolePrivilegesView | null;
  k2: EscalationCheck | null;
  schema: RuntimeSchemaFacts | null;
  refusals: string[];
  warnings: string[];
  typed: string;
  state: string;
}

export interface RuntimeRoleResult {
  role: string;
  schema: string;
  created: boolean;
  connect: boolean;
  canCreate: boolean;
  tables: number;
  after: RolePrivilegesView | null;
  auditPending: boolean;
}

export function parseRuntimeRolePreview(body: unknown): RuntimeRolePreview | null {
  const doc = record(body);
  const typed = text(doc?.typed);
  const state = text(doc?.state);
  if (doc === null || doc.action !== 'runtime' || typed === null || state === null) return null;
  const k2 = record(doc.k2);
  const schema = record(doc.schema);
  return {
    environment: text(doc.environment),
    database: text(doc.database) ?? '',
    target: parsePrivileges(doc.target),
    k2:
      k2 === null
        ? null
        : {
            found: texts(k2.found),
            checked: texts(k2.checked),
            unchecked: texts(k2.unchecked),
            noConnect: count(k2.no_connect) ?? 0,
          },
    schema:
      schema === null
        ? null
        : {
            tables: count(schema.tables) ?? 0,
            protectedFound: texts(schema.protected_found),
            protectedMissing: texts(schema.protected_missing),
          },
    refusals: texts(doc.refusals),
    warnings: texts(doc.warnings),
    typed,
    state,
  };
}

export function parseRuntimeRoleResult(body: unknown): RuntimeRoleResult | null {
  const doc = record(body);
  const role = text(doc?.role);
  const schema = text(doc?.schema);
  if (doc === null || doc.action !== 'runtime' || role === null || schema === null) return null;
  return {
    role,
    schema,
    created: doc.created === true,
    connect: doc.connect === true,
    canCreate: doc.can_create === true,
    tables: count(doc.tables) ?? 0,
    after: parsePrivileges(doc.after),
    auditPending: doc.audit_pending === true,
  };
}

function runtimeBody(connectionId: string, request: RuntimeRoleRequest): Record<string, unknown> {
  return {
    connection_id: connectionId,
    role: request.role,
    schema: request.schema,
    protected_tables: request.protectedTables,
    create: request.create,
  };
}

/** `POST /api/write/database-manager/runtime-role/preview`: what the wizard would do; changes nothing. */
export async function previewRuntimeRole(
  fetcher: typeof fetch,
  connectionId: string,
  request: RuntimeRoleRequest,
): Promise<Result<RuntimeRolePreview>> {
  const result = await send<unknown>(
    fetcher,
    'POST',
    '/api/write/database-manager/runtime-role/preview',
    runtimeBody(connectionId, request),
  );
  if (!result.ok) return result;
  const preview = parseRuntimeRolePreview(result.value);
  return preview === null ? UNREADABLE : { ok: true, value: preview };
}

/**
 * `POST /api/write/database-manager/runtime-role/apply`: bound to the preview's `state` and the typed role name.
 * `password` only for a new role; it goes to the server once and is not kept by the page.
 */
export async function applyRuntimeRole(
  fetcher: typeof fetch,
  connectionId: string,
  request: RuntimeRoleRequest,
  confirm: { state: string; typed: string; password: string | null },
): Promise<Result<RuntimeRoleResult>> {
  const body = runtimeBody(connectionId, request);
  body.state = confirm.state;
  body.typed = confirm.typed;
  if (confirm.password !== null) body.password = confirm.password;
  const result = await send<unknown>(
    fetcher,
    'POST',
    '/api/write/database-manager/runtime-role/apply',
    body,
  );
  if (!result.ok) return result;
  const done = parseRuntimeRoleResult(result.value);
  return done === null ? UNREADABLE : { ok: true, value: done };
}
