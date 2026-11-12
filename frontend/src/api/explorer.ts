/**
 * The database explorer (STU-010, 03 sections 2 B and 6; backend: docs/architecture/explorer.md). Every route reads.
 *
 * - `GET /api/connections/{id}/catalog[?refresh=true]`: schemas and their objects, flat and sorted.
 * - `GET /api/connections/{id}/table?schema=&name=`: columns, keys, constraints, indexes, the row estimate. The query
 *   form always, so a name with `/` works too (I-6 of stu-010-review.md).
 * - `GET /api/connections/{id}/extensions` (PostgreSQL) and `.../sqlite-info` (SQLite).
 *
 * The parsers fail closed: an answer without the lists the page needs is `studio.answer_invalid`, never an empty
 * catalog; an unknown object kind is kept as `other` and shown, never dropped; numbers and times are used only when
 * they are what they claim to be. Every string here comes from the database and is shown escaped and capped
 * (`lib/visible.ts`, U-1).
 */
import { readProblem, OFFLINE, type Problem } from './problem';
import type { Result } from './profiles';
import { apiHeaders } from '../session';

export type Engine = 'sqlite' | 'postgresql';
export const OBJECT_KINDS = [
  'table',
  'view',
  'materialized_view',
  'sequence',
  'foreign_table',
] as const;
/** `other`: a kind this page does not know; it is listed in a group of its own (fail closed, review C-1 of #32). */
export type ObjectKind = (typeof OBJECT_KINDS)[number] | 'other';

export interface CatalogObject {
  schema: string;
  name: string;
  kind: ObjectKind;
}

export interface Catalog {
  connectionId: string;
  engine: Engine;
  schemas: string[];
  objects: CatalogObject[];
  /** When the server read the catalog (ms since the epoch); null when the value is not a valid time. */
  builtAt: number | null;
  cached: boolean;
}

export interface Column {
  name: string;
  type: string | null;
  /** null: the server did not say. */
  nullable: boolean | null;
  default: string | null;
  comment: string | null;
}

export interface ForeignKey {
  name: string | null;
  columns: string[];
  referredSchema: string;
  referredTable: string;
  referredColumns: string[];
}

export interface IncomingKey {
  name: string | null;
  schema: string;
  table: string;
  columns: string[];
  referredColumns: string[];
}

export interface NamedColumns {
  name: string | null;
  columns: string[];
}

export interface CheckConstraint {
  name: string | null;
  sqltext: string | null;
}

export interface Index {
  name: string | null;
  columns: string[];
  unique: boolean | null;
}

export interface TableDetail {
  schema: string;
  name: string;
  kind: ObjectKind;
  columns: Column[];
  primaryKey: NamedColumns | null;
  foreignKeys: ForeignKey[];
  referencedBy: IncomingKey[];
  uniqueConstraints: NamedColumns[];
  checkConstraints: CheckConstraint[];
  indexes: Index[];
  /** PostgreSQL's `pg_class.reltuples`; null when unknown (SQLite, a view, never analyzed, or not a valid count). */
  rowEstimate: number | null;
}

export interface Extension {
  name: string;
  version: string | null;
  schema: string | null;
  defaultVersion: string | null;
  updateAvailable: boolean;
}

export interface SqliteTableFacts {
  name: string;
  type: string | null;
  strict: boolean | null;
  withoutRowid: boolean | null;
}

export interface SqliteInfo {
  journalMode: string | null;
  userVersion: number | null;
  pageSize: number | null;
  pageCount: number | null;
  foreignKeys: boolean | null;
  fileSize: number | null;
  /** Four octal digits (`0600`); null for anything else. */
  fileMode: string | null;
  tables: SqliteTableFacts[];
}

/** The problem for an answer the page cannot read (fail closed). */
export const ANSWER_INVALID: Problem = {
  code: 'studio.answer_invalid',
  kind: 'internal',
  status: 0,
  logId: null,
};

/** At most this many entries of one list are read from an answer; the server never sends more for a real database. */
export const MAX_LIST = 5000;

type Doc = Record<string, unknown>;

function doc(value: unknown): Doc | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Doc)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function flag(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

/** A count: a safe integer of at least 0, else null. */
export function count(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

/** Seconds since the epoch as milliseconds, only if that is a time `Date` can show (C-1 of stu-014-ui-review.md). */
export function epochMs(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return null;
  const ms = value * 1000;
  return Number.isFinite(new Date(ms).getTime()) ? ms : null;
}

function list(value: unknown): unknown[] | null {
  return Array.isArray(value) ? value.slice(0, MAX_LIST) : null;
}

function texts(value: unknown): string[] {
  return (list(value) ?? []).filter((item): item is string => typeof item === 'string');
}

function kind(value: unknown): ObjectKind {
  return OBJECT_KINDS.includes(value as (typeof OBJECT_KINDS)[number])
    ? (value as ObjectKind)
    : 'other';
}

export function parseCatalog(body: unknown): Catalog | null {
  const root = doc(body);
  if (root === null) return null;
  const engine = root.engine;
  const schemas = list(root.schemas);
  const objects = list(root.objects);
  if ((engine !== 'sqlite' && engine !== 'postgresql') || schemas === null || objects === null) {
    return null;
  }
  const parsed: CatalogObject[] = [];
  for (const item of objects) {
    const entry = doc(item);
    const schema = text(entry?.schema);
    const name = text(entry?.name);
    // An object without a schema or a name cannot be shown or asked for: the whole answer is refused.
    if (entry === null || schema === null || name === null) return null;
    parsed.push({ schema, name, kind: kind(entry.kind) });
  }
  const names = schemas.filter((item): item is string => typeof item === 'string');
  // A schema that holds objects is listed even when the server forgot it in `schemas`.
  for (const item of parsed) if (!names.includes(item.schema)) names.push(item.schema);
  return {
    connectionId: text(root.connection_id) ?? '',
    engine,
    schemas: names,
    objects: parsed,
    builtAt: epochMs(root.built_at),
    cached: root.cached === true,
  };
}

function namedColumns(value: unknown): NamedColumns | null {
  const entry = doc(value);
  if (entry === null) return null;
  return { name: text(entry.name), columns: texts(entry.columns) };
}

function entries<T>(value: unknown, read: (entry: Doc) => T | null): T[] {
  return (list(value) ?? []).flatMap((item) => {
    const entry = doc(item);
    const parsed = entry === null ? null : read(entry);
    return parsed === null ? [] : [parsed];
  });
}

export function parseTable(body: unknown): TableDetail | null {
  const root = doc(body);
  if (root === null) return null;
  const schema = text(root.schema);
  const name = text(root.name);
  const objectKind = kind(root.kind);
  if (schema === null || name === null) return null;
  // A sequence has no columns; anything else without a column list is not a detail the page can show.
  const columns = list(root.columns);
  if (columns === null && objectKind !== 'sequence') return null;
  return {
    schema,
    name,
    kind: objectKind,
    columns: entries(columns, (entry) => {
      const column = text(entry.name);
      return column === null
        ? null
        : {
            name: column,
            type: text(entry.type),
            nullable: flag(entry.nullable),
            default: text(entry.default),
            comment: text(entry.comment),
          };
    }),
    primaryKey: namedColumns(root.primary_key),
    foreignKeys: entries(root.foreign_keys, (entry) => {
      const table = text(entry.referred_table);
      return table === null
        ? null
        : {
            name: text(entry.name),
            columns: texts(entry.columns),
            referredSchema: text(entry.referred_schema) ?? schema,
            referredTable: table,
            referredColumns: texts(entry.referred_columns),
          };
    }),
    referencedBy: entries(root.referenced_by, (entry) => {
      const from = text(entry.schema);
      const table = text(entry.table);
      return from === null || table === null
        ? null
        : {
            name: text(entry.name),
            schema: from,
            table,
            columns: texts(entry.columns),
            referredColumns: texts(entry.referred_columns),
          };
    }),
    uniqueConstraints: entries(root.unique_constraints, (entry) => namedColumns(entry)),
    checkConstraints: entries(root.check_constraints, (entry) => ({
      name: text(entry.name),
      sqltext: text(entry.sqltext),
    })),
    indexes: entries(root.indexes, (entry) => ({
      name: text(entry.name),
      columns: texts(entry.columns),
      unique: flag(entry.unique),
    })),
    rowEstimate: count(root.row_estimate),
  };
}

export function parseExtensions(body: unknown): Extension[] | null {
  const root = doc(body);
  const items = list(root?.extensions);
  if (items === null) return null;
  return entries(items, (entry) => {
    const name = text(entry.name);
    return name === null
      ? null
      : {
          name,
          version: text(entry.version),
          schema: text(entry.schema),
          defaultVersion: text(entry.default_version),
          updateAvailable: entry.update_available === true,
        };
  });
}

export function parseSqliteInfo(body: unknown): SqliteInfo | null {
  const root = doc(body);
  if (root === null) return null;
  const mode = text(root.file_mode);
  return {
    journalMode: text(root.journal_mode),
    userVersion:
      typeof root.user_version === 'number' && Number.isSafeInteger(root.user_version)
        ? root.user_version
        : null,
    pageSize: count(root.page_size),
    pageCount: count(root.page_count),
    foreignKeys: flag(root.foreign_keys),
    fileSize: count(root.file_size),
    fileMode: mode !== null && /^[0-7]{4}$/.test(mode) ? mode : null,
    tables: entries(root.tables, (entry) => {
      const name = text(entry.name);
      return name === null
        ? null
        : {
            name,
            type: text(entry.type),
            strict: flag(entry.strict),
            withoutRowid: flag(entry.without_rowid),
          };
    }),
  };
}

async function read<T>(
  fetcher: typeof fetch,
  path: string,
  parse: (body: unknown) => T | null,
): Promise<Result<T>> {
  let response: Response;
  try {
    response = await fetcher(path, {
      cache: 'no-store',
      credentials: 'same-origin',
      headers: apiHeaders(),
    });
  } catch {
    return { ok: false, problem: OFFLINE };
  }
  if (!response.ok) return { ok: false, problem: await readProblem(response) };
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return { ok: false, problem: ANSWER_INVALID };
  }
  const value = parse(body);
  return value === null ? { ok: false, problem: ANSWER_INVALID } : { ok: true, value };
}

function base(connectionId: string): string {
  return `/api/connections/${encodeURIComponent(connectionId)}`;
}

export function fetchCatalog(
  fetcher: typeof fetch,
  connectionId: string,
  refresh = false,
): Promise<Result<Catalog>> {
  return read(
    fetcher,
    `${base(connectionId)}/catalog${refresh ? '?refresh=true' : ''}`,
    parseCatalog,
  );
}

export function fetchTable(
  fetcher: typeof fetch,
  connectionId: string,
  schema: string,
  name: string,
): Promise<Result<TableDetail>> {
  const query = new URLSearchParams({ schema, name });
  return read(fetcher, `${base(connectionId)}/table?${query.toString()}`, parseTable);
}

export function fetchExtensions(
  fetcher: typeof fetch,
  connectionId: string,
): Promise<Result<Extension[]>> {
  return read(fetcher, `${base(connectionId)}/extensions`, parseExtensions);
}

export function fetchSqliteInfo(
  fetcher: typeof fetch,
  connectionId: string,
): Promise<Result<SqliteInfo>> {
  return read(fetcher, `${base(connectionId)}/sqlite-info`, parseSqliteInfo);
}
