/**
 * Diagnostics (STU-014, 03 sections 2 G and 6): tabrion's findings and Studio's `STU-DX-*` for one open connection.
 *
 * - `POST /api/connections {profile_id}` opens (or returns) the read-only connection of a profile (STU-009).
 * - `POST /api/connections/{id}/diagnostics {}` runs the diagnosis. It only reads; it is a POST because it runs checks
 *   against the database. The answer is `{connection_id, engine, environment, ran_at, findings: [{code, severity,
 *   title, detail, remedy, source}], counts}`.
 * - `POST /api/connections/{id}/diagnostics/cancel {}` stops the running diagnoses of that connection; their answers
 *   then carry `STU-DX-008` ("Not every check ran").
 *
 * Fields the page does not know (such as `backup` of STU-025) are ignored. The parser fails closed: an answer without a findings list is no answer (`studio.answer_invalid`), an unknown
 * severity stays visible as `unknown` and is never counted as a lesser one, and the page counts the findings itself
 * instead of trusting `counts`. Titles, details and remedies are the server's English text and may name database
 * objects (tabrion names schemas and extensions), so the page shows them escaped and capped (`lib/visible.ts`, U-1).
 */
import type { Problem } from './problem';
import { send, type Result } from './profiles';
import type { Environment } from '../workspace';

export type Severity = 'error' | 'warning' | 'info' | 'unknown';
export type FindingSource = 'tabrion' | 'studio' | 'unknown';

export interface Finding {
  code: string;
  severity: Severity;
  title: string;
  detail: string;
  remedy: string;
  source: FindingSource;
}

export interface Diagnosis {
  connectionId: string;
  engine: string;
  /** The connection's environment as the server names it; null when it is not one Studio knows. */
  environment: Environment | null;
  /** When the diagnosis ran, in milliseconds since the epoch; null without a valid time. */
  ranAt: number | null;
  /** In the server's order (severity, then code), with unknown severities first. */
  findings: Finding[];
}

/** The problem for an answer the page cannot read (it never shows a half-read diagnosis as complete). */
export const ANSWER_INVALID: Problem = {
  code: 'studio.answer_invalid',
  kind: 'internal',
  status: 0,
  logId: null,
};

const SEVERITIES: readonly Severity[] = ['error', 'warning', 'info'];
const SOURCES: readonly FindingSource[] = ['tabrion', 'studio'];
const ENVIRONMENTS: readonly Environment[] = ['dev', 'test', 'prod'];
/** Display order: an unknown severity is shown with the errors, never below a warning (fail closed). */
const RANK: Record<Severity, number> = { unknown: 0, error: 0, warning: 1, info: 2 };

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function parseFinding(item: unknown): Finding[] {
  if (typeof item !== 'object' || item === null) return [];
  const doc = item as Record<string, unknown>;
  if (typeof doc.code !== 'string' || doc.code === '') return [];
  return [
    {
      code: doc.code,
      severity: SEVERITIES.includes(doc.severity as Severity)
        ? (doc.severity as Severity)
        : 'unknown',
      title: text(doc.title),
      detail: text(doc.detail),
      remedy: text(doc.remedy),
      source: SOURCES.includes(doc.source as FindingSource)
        ? (doc.source as FindingSource)
        : 'unknown',
    },
  ];
}

/** The diagnosis, or null for an answer without a findings list. */
export function parseDiagnosis(body: unknown): Diagnosis | null {
  if (typeof body !== 'object' || body === null) return null;
  const doc = body as Record<string, unknown>;
  if (!Array.isArray(doc.findings)) return null;
  const findings = doc.findings.flatMap(parseFinding);
  // A stable sort keeps the server's order within one rank.
  findings.sort((a, b) => RANK[a.severity] - RANK[b.severity]);
  // Only a time a Date can hold (at most 8.64e15 ms): a larger one would throw when it is formatted (C-1).
  const ms =
    typeof doc.ran_at === 'number' && Number.isFinite(doc.ran_at) && doc.ran_at > 0
      ? Math.round(doc.ran_at * 1000)
      : NaN;
  const ranAt = Number.isFinite(new Date(ms).getTime()) ? ms : null;
  return {
    connectionId: text(doc.connection_id),
    engine: text(doc.engine),
    environment: ENVIRONMENTS.includes(doc.environment as Environment)
      ? (doc.environment as Environment)
      : null,
    ranAt,
    findings,
  };
}

/** How many findings of each severity (the page counts; it does not trust the server's `counts`). */
export function countFindings(findings: Finding[]): Record<Severity, number> {
  const counts: Record<Severity, number> = { error: 0, warning: 0, info: 0, unknown: 0 };
  for (const finding of findings) counts[finding.severity] += 1;
  return counts;
}

function diagnosticsPath(connectionId: string): string {
  return `/api/connections/${encodeURIComponent(connectionId)}/diagnostics`;
}

export async function runDiagnostics(
  fetcher: typeof fetch,
  connectionId: string,
): Promise<Result<Diagnosis>> {
  const result = await send<unknown>(fetcher, 'POST', diagnosticsPath(connectionId), {});
  if (!result.ok) return result;
  const diagnosis = parseDiagnosis(result.value);
  return diagnosis === null
    ? { ok: false, problem: ANSWER_INVALID }
    : { ok: true, value: diagnosis };
}

/** Stop the running diagnoses of a connection; whether one was running. */
export async function cancelDiagnostics(
  fetcher: typeof fetch,
  connectionId: string,
): Promise<boolean> {
  const result = await send<{ canceled?: unknown }>(
    fetcher,
    'POST',
    `${diagnosticsPath(connectionId)}/cancel`,
    {},
  );
  return result.ok && result.value?.canceled === true;
}
