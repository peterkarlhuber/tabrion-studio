/**
 * Diagnostics (STU-014, 03 sections 2 G and 6): tabrion's findings and Studio's `STU-DX-*` for one open connection.
 *
 * - `POST /api/connections {profile_id}` opens (or returns) the read-only connection of a profile (STU-009).
 * - `POST /api/connections/{id}/diagnostics {}` runs the diagnosis. It only reads; it is a POST because it runs checks
 *   against the database. The answer is `{connection_id, engine, environment, ran_at, findings: [{code, severity,
 *   title, detail, remedy, source}], counts}`.
 * - `POST /api/connections/{id}/diagnostics/cancel {}` stops the running diagnoses of that connection; their answers
 *   then carry `STU-DX-008` ("Not every check ran").
 * - `POST /api/connections/{id}/diagnostics/report {format}` (STU-025, TD-036) runs the diagnosis again and answers the
 *   report as a file: `text/markdown` or `application/json`, named in `Content-Disposition`. The page keeps that name
 *   only when it has the server's form (`tabrion-studio-diagnosis-…`); otherwise it uses a fixed one.
 *
 * The answer's `backup: {hints, facts}` (STU-025) holds the backup hints `STU-BK-*`, which are not findings and not in
 * `counts`; the page reads the hints and ignores the facts. Fields the page does not know are ignored. The parser fails
 * closed: an answer without a findings list is no answer (`studio.answer_invalid`), a `backup` without a hints list is
 * shown as unreadable (never as "no hints"), an unknown severity stays visible as `unknown` and is never counted as a
 * lesser one, and the page counts the findings itself instead of trusting `counts`. Titles, details and remedies are the server's English text and may name database
 * objects (tabrion names schemas and extensions), so the page shows them escaped and capped (`lib/visible.ts`, U-1).
 */
import { apiHeaders } from '../session';
import { OFFLINE, readProblem, type Problem } from './problem';
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
  /** The backup hints (STU-025), sorted like the findings; null when the answer has none (an older server). */
  backup: Backup | null;
}

export interface Backup {
  hints: Finding[];
  /** True when `backup` was there but its hints could not be read: the page says so instead of "no hints". */
  invalid: boolean;
  /**
   * False when the facts say the backups were not looked at (SQLite `searched: false`, PostgreSQL `archive_mode:
   * null`): after a cancel or the time limit, without a file, or with an unreadable archiver (L-1 of the TD-036
   * review). Then no hints is not "nothing to point out".
   */
  checked: boolean;
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

function sortFindings(findings: Finding[]): Finding[] {
  // A stable sort keeps the server's order within one rank.
  return findings.sort((a, b) => RANK[a.severity] - RANK[b.severity]);
}

function parseBackup(value: unknown): Backup | null {
  if (value === undefined || value === null) return null;
  const doc = (typeof value === 'object' ? value : {}) as Record<string, unknown>;
  if (!Array.isArray(doc.hints)) return { hints: [], invalid: true, checked: false };
  const facts = (typeof doc.facts === 'object' && doc.facts !== null ? doc.facts : {}) as Record<
    string,
    unknown
  >;
  const checked = !(
    facts.searched === false ||
    ('archive_mode' in facts && facts.archive_mode === null)
  );
  return { hints: sortFindings(doc.hints.flatMap(parseFinding)), invalid: false, checked };
}

/** The diagnosis, or null for an answer without a findings list. */
export function parseDiagnosis(body: unknown): Diagnosis | null {
  if (typeof body !== 'object' || body === null) return null;
  const doc = body as Record<string, unknown>;
  if (!Array.isArray(doc.findings)) return null;
  const findings = sortFindings(doc.findings.flatMap(parseFinding));
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
    backup: parseBackup(doc.backup),
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

export type ReportFormat = 'markdown' | 'json';

export interface Report {
  filename: string;
  blob: Blob;
}

const REPORT_TYPES: Record<ReportFormat, string> = {
  markdown: 'text/markdown',
  json: 'application/json',
};
const REPORT_SUFFIXES: Record<ReportFormat, string> = { markdown: '.md', json: '.json' };
/** The server's names: `tabrion-studio-diagnosis-<engine>-<environment>-<UTC time>` (letters, digits, `-`). */
const REPORT_NAME = /^tabrion-studio-diagnosis(?:-[A-Za-z0-9]{1,32}){1,4}$/;

/** The name to save a report under: the server's when it has the expected form, else a fixed one. */
export function reportFilename(disposition: string | null, format: ReportFormat): string {
  const suffix = REPORT_SUFFIXES[format];
  const name =
    /^attachment;\s*filename="([^"]{1,200})"$/.exec(disposition?.trim() ?? '')?.[1] ?? '';
  return name.endsWith(suffix) && REPORT_NAME.test(name.slice(0, -suffix.length))
    ? name
    : `tabrion-studio-diagnosis${suffix}`;
}

/**
 * The diagnosis report of an open connection as a file. A refusal is its problem (`studio.report_unavailable`, the
 * connection's problems); an answer of another media type is `studio.answer_invalid`, so the page never saves
 * something it did not ask for.
 */
export async function downloadReport(
  fetcher: typeof fetch,
  connectionId: string,
  format: ReportFormat,
  signal?: AbortSignal,
): Promise<Result<Report>> {
  try {
    const response = await fetcher(`${diagnosticsPath(connectionId)}/report`, {
      signal,
      method: 'POST',
      cache: 'no-store',
      credentials: 'same-origin',
      headers: { ...apiHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ format }),
    });
    if (!response.ok) return { ok: false, problem: await readProblem(response) };
    const type = (response.headers.get('Content-Type') ?? '').split(';')[0]?.trim().toLowerCase();
    if (type !== REPORT_TYPES[format]) return { ok: false, problem: ANSWER_INVALID };
    const body = await response.arrayBuffer();
    return {
      ok: true,
      value: {
        filename: reportFilename(response.headers.get('Content-Disposition'), format),
        blob: new Blob([body], { type: REPORT_TYPES[format] }),
      },
    };
  } catch {
    return { ok: false, problem: OFFLINE };
  }
}
