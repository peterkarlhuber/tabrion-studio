/**
 * Vector data (STU-055, `docs/architecture/vector-inspection.md`): `GET /api/connections/{id}/vectors` on an open,
 * read-only connection. The answer lists the vector columns with their indexes (ANN or exact search), dimensions and
 * bounded counts, the embedding identities the database's metadata names, the pgvector / sqlite-vec status and hints
 * (`{code, severity, params}`, worded by the page in en/de).
 *
 * The parser fails closed: an answer without its lists is no answer (`studio.answer_invalid`); a column whose search
 * is not exactly `ann` is shown as exact search (never as ANN-indexed); an unknown extension state is `unknown`; a
 * count of an unknown kind is "unknown". Names come from the database and are shown escaped (`lib/visible.ts`).
 */
import { apiHeaders } from '../session';
import { ANSWER_INVALID } from './diagnostics';
import { OFFLINE, readProblem } from './problem';
import type { Result } from './profiles';

export type ExtensionState =
  'installed' | 'available' | 'missing' | 'other_schema' | 'not_determinable' | 'unknown';
export type CountKind =
  'exact' | 'more_than' | 'estimate' | 'timeout' | 'unreadable' | 'skipped' | 'unknown';

export interface Count {
  value: number | null;
  kind: CountKind;
}

export interface VectorIndex {
  name: string;
  method: string;
  /** `valid`, `invalid`, `building` or `unknown` (a `vec0` table has no index state). */
  state: string;
  ann: boolean;
  operatorClasses: string[];
  options: string[];
  sizeBytes: number | null;
  createdWith: string | null;
  /** An index on an expression of the column, or on part of the rows: a plain query may not use it. */
  expression: boolean;
  partial: boolean;
}

export interface VectorColumn {
  schema: string;
  table: string;
  column: string;
  type: string;
  /** `pgvector`, `float_array`, `blob` or `vec0`. */
  kind: string;
  /** `orkexa` or `generic`. */
  layout: string;
  dimensions: number | null;
  search: 'ann' | 'exact';
  rows: Count | null;
  indexes: VectorIndex[];
}

export interface Identity {
  schema: string;
  table: string;
  tenant: string | null;
  namespace: string | null;
  space: string;
  state: string | null;
  provider: string | null;
  model: string | null;
  digest: string | null;
  normalized: boolean | null;
  dimensions: number | null;
  complete: boolean;
  embeddings: Count | null;
}

export interface VectorHint {
  code: string;
  severity: 'warning' | 'info';
  params: Record<string, unknown>;
}

export interface VectorExtension {
  name: string;
  state: ExtensionState;
  installedVersion: string | null;
  availableVersion: string | null;
  schema: string | null;
  updateAvailable: boolean;
}

export interface VectorReport {
  connectionId: string;
  engine: string;
  extension: VectorExtension;
  columns: VectorColumn[];
  identities: Identity[];
  hints: VectorHint[];
  exactCountMax: number;
}

const EXTENSION_STATES: readonly ExtensionState[] = [
  'installed',
  'available',
  'missing',
  'other_schema',
  'not_determinable',
];
const COUNT_KINDS: readonly CountKind[] = [
  'exact',
  'more_than',
  'estimate',
  'timeout',
  'unreadable',
  'skipped',
];

type Json = Record<string, unknown>;

function isObject(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function int(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) ? value : null;
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

function parseCount(value: unknown): Count | null {
  if (!isObject(value)) return null;
  const kind = COUNT_KINDS.find((item) => item === value.kind) ?? 'unknown';
  return { value: int(value.value), kind };
}

function parseIndex(value: unknown): VectorIndex | null {
  if (!isObject(value)) return null;
  const name = text(value.name);
  if (name === null) return null;
  const state = text(value.state) ?? 'unknown';
  return {
    name,
    method: text(value.method) ?? '',
    state,
    // An ANN method counts only as the server states it; the page never infers one.
    ann: value.ann === true,
    operatorClasses: strings(value.operator_classes),
    options: strings(value.options),
    sizeBytes: int(value.size_bytes),
    createdWith: text(value.created_with),
    expression: value.expression === true,
    partial: value.partial === true,
  };
}

function parseColumn(value: unknown): VectorColumn | null {
  if (!isObject(value)) return null;
  const [schema, table, column] = [text(value.schema), text(value.table), text(value.column)];
  if (schema === null || table === null || column === null) return null;
  const indexes = Array.isArray(value.indexes)
    ? value.indexes.map(parseIndex).filter((item): item is VectorIndex => item !== null)
    : [];
  return {
    schema,
    table,
    column,
    type: text(value.type) ?? '',
    kind: text(value.kind) ?? '',
    layout: text(value.layout) ?? 'generic',
    dimensions: int(value.dimensions),
    // Fail closed: anything but an explicit "ann" with a valid ANN index listed is exact search.
    search:
      value.search === 'ann' && indexes.some((index) => index.ann && index.state === 'valid')
        ? 'ann'
        : 'exact',
    rows: parseCount(value.rows),
    indexes,
  };
}

function parseIdentity(value: unknown): Identity | null {
  if (!isObject(value)) return null;
  const space = text(value.space);
  if (space === null) return null;
  return {
    schema: text(value.schema) ?? '',
    table: text(value.table) ?? '',
    tenant: text(value.tenant),
    namespace: text(value.namespace),
    space,
    state: text(value.state),
    provider: text(value.provider),
    model: text(value.model),
    digest: text(value.digest),
    normalized: typeof value.normalized === 'boolean' ? value.normalized : null,
    dimensions: int(value.dimensions),
    complete: value.complete === true,
    embeddings: parseCount(value.embeddings),
  };
}

function parseHint(value: unknown): VectorHint | null {
  if (!isObject(value)) return null;
  const code = text(value.code);
  if (code === null) return null;
  return {
    code,
    severity: value.severity === 'info' ? 'info' : 'warning',
    params: isObject(value.params) ? value.params : {},
  };
}

function parseExtension(value: unknown): VectorExtension {
  const doc = isObject(value) ? value : {};
  return {
    name: text(doc.name) ?? '',
    state: EXTENSION_STATES.find((item) => item === doc.state) ?? 'unknown',
    installedVersion: text(doc.installed_version),
    availableVersion: text(doc.available_version),
    schema: text(doc.schema),
    updateAvailable: doc.update_available === true,
  };
}

export function parseVectors(value: unknown): VectorReport | null {
  if (!isObject(value)) return null;
  const { columns, identities, hints } = value;
  if (!Array.isArray(columns) || !Array.isArray(identities) || !Array.isArray(hints)) return null;
  const connectionId = text(value.connection_id);
  if (connectionId === null) return null;
  const limits = isObject(value.limits) ? value.limits : {};
  return {
    connectionId,
    engine: text(value.engine) ?? '',
    extension: parseExtension(value.extension),
    columns: columns.map(parseColumn).filter((item): item is VectorColumn => item !== null),
    identities: identities.map(parseIdentity).filter((item): item is Identity => item !== null),
    hints: hints.map(parseHint).filter((item): item is VectorHint => item !== null),
    exactCountMax: int(limits.exact_count_max) ?? 100_000,
  };
}

export async function fetchVectors(
  fetcher: typeof fetch,
  connectionId: string,
): Promise<Result<VectorReport>> {
  try {
    const response = await fetcher(`/api/connections/${encodeURIComponent(connectionId)}/vectors`, {
      cache: 'no-store',
      credentials: 'same-origin',
      headers: apiHeaders(),
    });
    if (!response.ok) return { ok: false, problem: await readProblem(response) };
    const report = parseVectors(await response.json());
    return report === null ? { ok: false, problem: ANSWER_INVALID } : { ok: true, value: report };
  } catch {
    return { ok: false, problem: OFFLINE };
  }
}
