/**
 * The Schema Designer's read-only schema comparison (STU-XSD-2; STU-ADR-0007 sections 1 to 3, gate G-S3, not
 * granted): `POST /api/write/schema-designer/compare`. The route answers only while the `schema_designer` flag is on.
 *
 * Each side is a schema the server reads now on an open read-only connection, this session's draft (held by the
 * server), or the text of a snapshot file the user picked (`SchemaSnapshot.to_json`, level 2). The page sends the
 * file's text as it is, never a model of its own; the server reads it with Tabrion's parser and only compares and
 * shows it. A plan preview, when asked for, is shown only: it has no plan hash and nothing can confirm or run it.
 */
import { issues as problemIssues, type Problem } from './problem';
import { send, type Result } from './profiles';
import type { PlanStep, PlanWarning } from './schemaDesigner';

/** The most bytes of a snapshot file's text (the server's limit; the page checks it before it reads the file). */
export const MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024;

export type CompareSide =
  | { source: 'connection'; connectionId: string; schema: string }
  | { source: 'draft'; connectionId: string }
  | { source: 'file'; text: string };

/** A feature marker of Tabrion's snapshot (D-5): its kind (`unknown` for one Studio does not know) and its name. */
export interface FeatureMarker {
  kind: string;
  name: string | null;
  known: boolean;
  marker: string;
}

export interface FlaggedTable {
  table: string;
  features: FeatureMarker[];
}

export interface SideSummary {
  source: string;
  engine: string;
  schema: string | null;
  environment: string | null;
  fingerprint: string;
  tables: number;
  views: number;
  triggers: number;
  policies: number;
  rowSecurity: number;
  features: FeatureMarker[];
  flagged: FlaggedTable[];
}

export interface Difference {
  kind: string;
  table: string;
  column: string | null;
  name: string | null;
  breaking: boolean;
}

export type ComparePlan =
  | {
      status: 'ready';
      dialect: string;
      serverVersion: number[];
      sourceFingerprint: string;
      targetFingerprint: string;
      risks: string[];
      transactional: boolean;
      steps: PlanStep[];
      warnings: PlanWarning[];
    }
  | { status: 'refused'; problem: Problem };

export type Verdict = 'same' | 'different' | 'inconclusive';

export interface Comparison {
  expected: SideSummary;
  actual: SideSummary;
  verdict: Verdict;
  sameFingerprint: boolean;
  fingerprintOnly: boolean;
  flagged: boolean;
  unknownFeatures: boolean;
  differences: Difference[];
  breaking: number;
  plan: ComparePlan | null;
}

type Doc = Record<string, unknown>;

function record(value: unknown): Doc | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Doc)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function strings(value: unknown): string[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
    ? (value as string[])
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

function parseMarker(doc: Doc): FeatureMarker | null {
  if (typeof doc.kind !== 'string' || typeof doc.marker !== 'string') return null;
  return { kind: doc.kind, name: text(doc.name), known: doc.known === true, marker: doc.marker };
}

function count(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null;
}

function parseSide(doc: Doc | null): SideSummary | null {
  if (doc === null) return null;
  const features = list(doc.features, parseMarker);
  const flagged = list(doc.flagged, (item) => {
    const markers = list(item.features, parseMarker);
    return typeof item.table === 'string' && markers !== null
      ? { table: item.table, features: markers }
      : null;
  });
  const numbers = [doc.tables, doc.views, doc.triggers, doc.policies, doc.row_security].map(count);
  const [tables, views, triggers, policies, rowSecurity] = numbers;
  if (
    features === null ||
    flagged === null ||
    tables == null ||
    views == null ||
    triggers == null ||
    policies == null ||
    rowSecurity == null ||
    typeof doc.source !== 'string' ||
    typeof doc.engine !== 'string' ||
    typeof doc.fingerprint !== 'string'
  ) {
    return null;
  }
  return {
    source: doc.source,
    engine: doc.engine,
    schema: text(doc.schema),
    environment: text(doc.environment),
    fingerprint: doc.fingerprint,
    tables,
    views,
    triggers,
    policies,
    rowSecurity,
    features,
    flagged,
  };
}

function parseStep(item: Doc): PlanStep | null {
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
}

function parsePlan(value: unknown): ComparePlan | null | undefined {
  if (value === null) return null;
  const doc = record(value);
  if (doc === null) return undefined;
  if (doc.status === 'refused') {
    const problem = record(doc.problem);
    if (problem === null || typeof problem.code !== 'string') return undefined;
    return {
      status: 'refused',
      problem: {
        code: problem.code,
        kind: 'request',
        status: 400,
        logId: null,
        issues: problemIssues(problem.issues) ?? [],
      },
    };
  }
  const steps = list(doc.steps, parseStep);
  const risks = strings(doc.risks);
  const version = Array.isArray(doc.server_version) ? doc.server_version : null;
  const warnings = list(doc.warnings, (item) =>
    typeof item.code === 'string'
      ? {
          code: item.code,
          table: text(item.table),
          lockTimeoutS: typeof item.lock_timeout_s === 'number' ? item.lock_timeout_s : null,
        }
      : null,
  );
  if (
    doc.status !== 'ready' ||
    steps === null ||
    risks === null ||
    warnings === null ||
    version === null ||
    !version.every((part) => typeof part === 'number') ||
    typeof doc.dialect !== 'string' ||
    typeof doc.source_fingerprint !== 'string' ||
    typeof doc.target_fingerprint !== 'string'
  ) {
    return undefined;
  }
  return {
    status: 'ready',
    dialect: doc.dialect,
    serverVersion: version as number[],
    sourceFingerprint: doc.source_fingerprint,
    targetFingerprint: doc.target_fingerprint,
    risks,
    transactional: doc.transactional === true,
    steps,
    warnings,
  };
}

const VERDICTS = new Set<string>(['same', 'different', 'inconclusive']);

export function parseComparison(body: unknown): Comparison | null {
  const doc = record(body);
  if (doc === null) return null;
  const expected = parseSide(record(doc.expected));
  const actual = parseSide(record(doc.actual));
  const differences = list(doc.differences, (item) =>
    typeof item.kind === 'string' && typeof item.table === 'string'
      ? {
          kind: item.kind,
          table: item.table,
          column: text(item.column),
          name: text(item.name),
          breaking: item.breaking === true,
        }
      : null,
  );
  const plan = parsePlan(doc.plan);
  if (
    expected === null ||
    actual === null ||
    differences === null ||
    plan === undefined ||
    typeof doc.verdict !== 'string' ||
    !VERDICTS.has(doc.verdict) ||
    typeof doc.breaking !== 'number'
  ) {
    return null;
  }
  return {
    expected,
    actual,
    verdict: doc.verdict as Verdict,
    sameFingerprint: doc.same_fingerprint === true,
    fingerprintOnly: doc.fingerprint_only === true,
    flagged: doc.flagged === true,
    unknownFeatures: doc.unknown_features === true,
    differences,
    breaking: doc.breaking,
    plan,
  };
}

function sideBody(side: CompareSide): Record<string, unknown> {
  if (side.source === 'file') return { source: 'file', text: side.text };
  if (side.source === 'draft') return { source: 'draft', connection_id: side.connectionId };
  const body: Record<string, unknown> = { source: 'connection', connection_id: side.connectionId };
  if (side.schema !== '') body.schema = side.schema;
  return body;
}

const UNREADABLE: Result<never> = {
  ok: false,
  problem: { code: 'studio.answer_invalid', kind: 'internal', status: 0, logId: null },
};

/** `POST /api/write/schema-designer/compare`: compare two sides and, when asked, preview the plan between them. */
export async function compareSchemas(
  fetcher: typeof fetch,
  expected: CompareSide,
  actual: CompareSide,
  plan: boolean,
): Promise<Result<Comparison>> {
  const result = await send<unknown>(fetcher, 'POST', '/api/write/schema-designer/compare', {
    expected: sideBody(expected),
    actual: sideBody(actual),
    plan,
  });
  if (!result.ok) return result;
  const comparison = parseComparison(result.value);
  return comparison === null ? UNREADABLE : { ok: true, value: comparison };
}
