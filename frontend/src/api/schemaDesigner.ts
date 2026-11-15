/**
 * The Schema Designer's database creation (STU-CR-2, STU-CR-3) and its table designer with the read-only plan
 * preview (STU-CR-4; STU-ADR-0007, gate G-S3, not granted). The routes
 * answer only while the owner of the Studio process turned the `schema_designer` flag on in `write-areas.toml`; the
 * page reads that state (`GET /api/write/areas`) to show the wizard at all and never sets it. The server decides
 * everything that matters: the folder and file checks and the mode of a SQLite file, the unlock, the stored
 * environment (never PROD) and the owner checks of a PostgreSQL database. The draft lives on the server; the page
 * sends one change at a time and never a model or a plan. The page builds no SQL.
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

// --- the designer's draft and its plan preview (STU-CR-4) ----------------------------------------------------------
//
// The draft (the target model) lives only on the server, bound to this session's unlock of the connection. The page
// sends one edit operation at a time and gets the whole draft back; it never sends a model, a snapshot or a plan
// (GS3-09). The preview is read only: no route of this version runs a plan.

export type ConstraintKind = 'primary_key' | 'unique' | 'foreign_key' | 'check';

export interface DraftColumn {
  name: string;
  type: string;
  nullable: boolean;
  default: string | null;
  identity: string | null;
  generated: string | null;
}

export interface DraftForeignKey {
  name: string | null;
  columns: string[];
  referredTable: string;
  referredColumns: string[];
  onDelete: string | null;
  onUpdate: string | null;
}

export interface DraftIndex {
  name: string;
  columns: string[];
  unique: boolean;
  where: string | null;
  expressions: number[];
}

export interface DraftTable {
  name: string;
  status: 'new' | 'changed' | 'unchanged';
  columns: DraftColumn[];
  primaryKey: string[];
  unique: { name: string | null; columns: string[] }[];
  foreignKeys: DraftForeignKey[];
  checks: { name: string | null; expression: string }[];
  indexes: DraftIndex[];
  triggers: number;
  policies: number;
  features: string[];
}

export interface Draft {
  connectionId: string;
  engine: 'sqlite' | 'postgresql';
  schema: string;
  revision: number;
  edits: number;
  baseFingerprint: string;
  draftFingerprint: string;
  changed: boolean;
  tables: DraftTable[];
  dropped: string[];
  limits: { tables: number; columns: number; elements: number; edits: number };
}

export interface PlanStatement {
  sql: string;
  target: string;
  transactional: boolean;
}

export interface PlanStep {
  index: number;
  operation: string;
  target: string;
  risks: string[];
  transactional: boolean;
  statements: PlanStatement[];
}

export interface PlanPreview {
  revision: number;
  dialect: string;
  serverVersion: number[];
  sourceFingerprint: string;
  targetFingerprint: string;
  planHash: string;
  risks: string[];
  transactional: boolean;
  baseChanged: boolean;
  steps: PlanStep[];
}

/** One edit operation as the server takes it (`op` and its own keys; the server checks every part). */
export type DraftEdit = { op: string } & Record<string, unknown>;

type Doc = Record<string, unknown>;

function record(value: unknown): Doc | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Doc)
    : null;
}

function strings(value: unknown): string[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
    ? (value as string[])
    : null;
}

function numbers(value: unknown): number[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === 'number')
    ? (value as number[])
    : null;
}

function list<T>(value: unknown, parse: (item: Doc) => T | null): T[] | null {
  if (!Array.isArray(value)) return null;
  const out: T[] = [];
  for (const item of value) {
    const entry = record(item);
    const parsed = entry === null ? null : parse(entry);
    if (parsed === null) return null;
    out.push(parsed);
  }
  return out;
}

function parseColumn(doc: Doc): DraftColumn | null {
  if (typeof doc.name !== 'string' || typeof doc.type !== 'string') return null;
  return {
    name: doc.name,
    type: doc.type,
    nullable: doc.nullable === true,
    default: text(doc.default),
    identity: text(doc.identity),
    generated: text(doc.generated),
  };
}

function parseTable(doc: Doc): DraftTable | null {
  const status = doc.status;
  const columns = list(doc.columns, parseColumn);
  const primaryKey = strings(doc.primary_key);
  const unique = list(doc.unique, (item) => {
    const cols = strings(item.columns);
    return cols === null ? null : { name: text(item.name), columns: cols };
  });
  const foreignKeys = list(doc.foreign_keys, (item) => {
    const cols = strings(item.columns);
    const referred = strings(item.referred_columns);
    if (cols === null || referred === null || typeof item.referred_table !== 'string') return null;
    return {
      name: text(item.name),
      columns: cols,
      referredTable: item.referred_table,
      referredColumns: referred,
      onDelete: text(item.on_delete),
      onUpdate: text(item.on_update),
    };
  });
  const checks = list(doc.checks, (item) =>
    typeof item.expression === 'string'
      ? { name: text(item.name), expression: item.expression }
      : null,
  );
  const indexes = list(doc.indexes, (item) => {
    const cols = strings(item.columns);
    const expressions = numbers(item.expressions);
    if (typeof item.name !== 'string' || cols === null || expressions === null) return null;
    return {
      name: item.name,
      columns: cols,
      unique: item.unique === true,
      where: text(item.where),
      expressions,
    };
  });
  const features = strings(doc.features);
  if (
    typeof doc.name !== 'string' ||
    (status !== 'new' && status !== 'changed' && status !== 'unchanged') ||
    columns === null ||
    primaryKey === null ||
    unique === null ||
    foreignKeys === null ||
    checks === null ||
    indexes === null ||
    features === null
  ) {
    return null;
  }
  return {
    name: doc.name,
    status,
    columns,
    primaryKey,
    unique,
    foreignKeys,
    checks,
    indexes,
    triggers: typeof doc.triggers === 'number' ? doc.triggers : 0,
    policies: typeof doc.policies === 'number' ? doc.policies : 0,
    features,
  };
}

export function parseDraft(body: unknown): Draft | null {
  const doc = record(body);
  if (doc === null) return null;
  const tables = list(doc.tables, parseTable);
  const dropped = strings(doc.dropped);
  const limits = record(doc.limits);
  if (
    typeof doc.connection_id !== 'string' ||
    (doc.engine !== 'sqlite' && doc.engine !== 'postgresql') ||
    typeof doc.schema !== 'string' ||
    typeof doc.revision !== 'number' ||
    typeof doc.edits !== 'number' ||
    typeof doc.base_fingerprint !== 'string' ||
    typeof doc.draft_fingerprint !== 'string' ||
    tables === null ||
    dropped === null ||
    limits === null
  ) {
    return null;
  }
  const limit = (key: string) => (typeof limits[key] === 'number' ? limits[key] : 0);
  return {
    connectionId: doc.connection_id,
    engine: doc.engine,
    schema: doc.schema,
    revision: doc.revision,
    edits: doc.edits,
    baseFingerprint: doc.base_fingerprint,
    draftFingerprint: doc.draft_fingerprint,
    changed: doc.changed === true,
    tables,
    dropped,
    limits: {
      tables: limit('tables'),
      columns: limit('columns'),
      elements: limit('elements'),
      edits: limit('edits'),
    },
  };
}

export function parsePreview(body: unknown): PlanPreview | null {
  const doc = record(body);
  if (doc === null) return null;
  const steps = list(doc.steps, (item) => {
    const risks = strings(item.risks);
    const statements = list(item.statements, (statement) =>
      typeof statement.sql === 'string' && typeof statement.target === 'string'
        ? {
            sql: statement.sql,
            target: statement.target,
            transactional: statement.transactional === true,
          }
        : null,
    );
    if (
      typeof item.index !== 'number' ||
      typeof item.operation !== 'string' ||
      typeof item.target !== 'string' ||
      risks === null ||
      statements === null
    ) {
      return null;
    }
    return {
      index: item.index,
      operation: item.operation,
      target: item.target,
      risks,
      transactional: item.transactional === true,
      statements,
    };
  });
  const risks = strings(doc.risks);
  const version = numbers(doc.server_version);
  if (
    steps === null ||
    risks === null ||
    version === null ||
    typeof doc.revision !== 'number' ||
    typeof doc.dialect !== 'string' ||
    typeof doc.source_fingerprint !== 'string' ||
    typeof doc.target_fingerprint !== 'string' ||
    typeof doc.plan_hash !== 'string'
  ) {
    return null;
  }
  return {
    revision: doc.revision,
    dialect: doc.dialect,
    serverVersion: version,
    sourceFingerprint: doc.source_fingerprint,
    targetFingerprint: doc.target_fingerprint,
    planHash: doc.plan_hash,
    risks,
    transactional: doc.transactional === true,
    baseChanged: doc.base_changed === true,
    steps,
  };
}

const DRAFT_ROUTE = '/api/write/schema-designer/draft';

async function draftCall(
  fetcher: typeof fetch,
  path: string,
  body: Record<string, unknown>,
): Promise<Result<Draft>> {
  const result = await send<unknown>(fetcher, 'POST', path, body);
  if (!result.ok) return result;
  const draft = parseDraft(result.value);
  return draft === null ? UNREADABLE : { ok: true, value: draft };
}

/** `POST /api/write/schema-designer/draft`: start (or start again) the draft of one schema of the connection. */
export function startDraft(
  fetcher: typeof fetch,
  connectionId: string,
  schema: string,
): Promise<Result<Draft>> {
  const body: Record<string, unknown> = { connection_id: connectionId };
  if (schema !== '') body.schema = schema;
  return draftCall(fetcher, DRAFT_ROUTE, body);
}

/** `POST /api/write/schema-designer/draft/view`: the draft as the server holds it. */
export function viewDraft(fetcher: typeof fetch, connectionId: string): Promise<Result<Draft>> {
  return draftCall(fetcher, `${DRAFT_ROUTE}/view`, { connection_id: connectionId });
}

/** `POST /api/write/schema-designer/draft/edits`: one edit on the revision the page shows. */
export function editDraft(
  fetcher: typeof fetch,
  connectionId: string,
  revision: number,
  edit: DraftEdit,
): Promise<Result<Draft>> {
  return draftCall(fetcher, `${DRAFT_ROUTE}/edits`, {
    connection_id: connectionId,
    revision,
    edit,
  });
}

/** `DELETE /api/write/schema-designer/draft`: drop the draft. */
export function discardDraft(fetcher: typeof fetch, connectionId: string): Promise<Result<null>> {
  return send<null>(fetcher, 'DELETE', DRAFT_ROUTE, { connection_id: connectionId });
}

/** `POST /api/write/schema-designer/draft/preview`: the read-only plan from a fresh snapshot to the draft. */
export async function previewDraft(
  fetcher: typeof fetch,
  connectionId: string,
): Promise<Result<PlanPreview>> {
  const result = await send<unknown>(fetcher, 'POST', `${DRAFT_ROUTE}/preview`, {
    connection_id: connectionId,
  });
  if (!result.ok) return result;
  const preview = parsePreview(result.value);
  return preview === null ? UNREADABLE : { ok: true, value: preview };
}
