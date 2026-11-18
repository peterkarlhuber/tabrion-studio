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
