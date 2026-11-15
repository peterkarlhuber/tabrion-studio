/**
 * The Schema Designer's database creation (STU-CR-2, STU-CR-3; STU-ADR-0007, gate G-S3, not granted). Both routes
 * answer only while the owner of the Studio process turned the `schema_designer` flag on in `write-areas.toml`; the
 * page reads that state (`GET /api/write/areas`) to show the wizard at all and never sets it. The server decides
 * everything that matters: the folder and file checks and the mode of a SQLite file, the unlock, the stored
 * environment (never PROD) and the owner checks of a PostgreSQL database. The page builds no SQL.
 */
import { send, type Result } from './profiles';

export interface CreatedSqliteFile {
  engine: 'sqlite';
  path: string;
  fileName: string;
  /** `0600` on POSIX; null on Windows (the folder's access list decides there). */
  mode: string | null;
  auditPending: boolean;
}

export interface CreatedPostgresDatabase {
  engine: 'postgresql';
  database: string;
  owner: string | null;
  auditPending: boolean;
}

export interface PostgresDatabaseFields {
  name: string;
  /** Empty: the write profile's role owns the new database. */
  owner: string;
  template: 'template1' | 'template0';
  encoding: string;
  /** Empty: the template's locale. */
  locale: string;
}

export const SQLITE_SUFFIXES = ['.db', '.sqlite', '.sqlite3'] as const;

/** The last part of a path as the server takes it (the name the user types to confirm). */
export function fileName(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] ?? '';
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

export function parseCreatedFile(body: unknown): CreatedSqliteFile | null {
  if (typeof body !== 'object' || body === null) return null;
  const doc = body as Record<string, unknown>;
  const path = text(doc.path);
  const name = text(doc.file_name);
  if (doc.engine !== 'sqlite' || path === null || name === null) return null;
  return {
    engine: 'sqlite',
    path,
    fileName: name,
    mode: text(doc.mode),
    auditPending: doc.audit_pending === true,
  };
}

export function parseCreatedDatabase(body: unknown): CreatedPostgresDatabase | null {
  if (typeof body !== 'object' || body === null) return null;
  const doc = body as Record<string, unknown>;
  const database = text(doc.database);
  if (doc.engine !== 'postgresql' || database === null) return null;
  return {
    engine: 'postgresql',
    database,
    owner: text(doc.owner),
    auditPending: doc.audit_pending === true,
  };
}

const UNREADABLE: Result<never> = {
  ok: false,
  problem: { code: 'studio.answer_invalid', kind: 'internal', status: 0, logId: null },
};

/** `POST /api/write/schema-designer/create/sqlite`: a new, empty, private SQLite file (STU-CR-2). */
export async function createSqliteFile(
  fetcher: typeof fetch,
  path: string,
  typed: string,
): Promise<Result<CreatedSqliteFile>> {
  const result = await send<unknown>(fetcher, 'POST', '/api/write/schema-designer/create/sqlite', {
    path,
    typed,
  });
  if (!result.ok) return result;
  const created = parseCreatedFile(result.value);
  return created === null ? UNREADABLE : { ok: true, value: created };
}

/**
 * `POST /api/write/schema-designer/create/postgresql`: a new database on this session's unlocked write connection of
 * a DEV or TEST server (STU-CR-3). Empty optional fields are left out, so the server's defaults apply.
 */
export async function createPostgresDatabase(
  fetcher: typeof fetch,
  connectionId: string,
  fields: PostgresDatabaseFields,
  typed: string,
): Promise<Result<CreatedPostgresDatabase>> {
  const body: Record<string, string> = {
    connection_id: connectionId,
    name: fields.name,
    template: fields.template,
    encoding: fields.encoding,
    typed,
  };
  if (fields.owner !== '') body.owner = fields.owner;
  if (fields.locale !== '') body.locale = fields.locale;
  const result = await send<unknown>(
    fetcher,
    'POST',
    '/api/write/schema-designer/create/postgresql',
    body,
  );
  if (!result.ok) return result;
  const created = parseCreatedDatabase(result.value);
  return created === null ? UNREADABLE : { ok: true, value: created };
}
