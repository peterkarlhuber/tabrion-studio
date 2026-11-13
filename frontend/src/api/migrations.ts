/**
 * Migration status and code trust (STU-015, 03 sections 2 F and 6, threat T-16).
 *
 * - `GET /api/project/code-trust`: may Studio run the project's Alembic revision files? (`state`, `digest`, the files)
 * - `POST /api/project/code-trust {digest}` trusts exactly the files the dialog showed (`studio.trust_stale` when
 *   they changed meanwhile); `DELETE` takes the trust back.
 * - `POST /api/connections {profile_id}` opens (or returns) the read-only connection of a profile (STU-009).
 * - `GET /api/connections/{id}/migrations`: the status per format; Studio never migrates.
 *
 * Names, paths and revision ids come from the project, so the page shows them escaped (`lib/visible.ts`, U-1).
 */
import { connectBody } from './connections';
import { readProblem, OFFLINE, type Problem } from './problem';
import type { EnvironmentTag } from './profiles';
import { getJson, send, type Result } from './profiles';
import { apiHeaders } from '../session';

export type CodeTrustState = 'none' | 'untrusted' | 'changed' | 'trusted' | 'invalid';

export interface CodeTrust {
  state: CodeTrustState;
  configFile: string | null;
  digest: string | null;
  scriptLocation: string | null;
  revisionFiles: string[];
  /** The project modules in the `prepend_sys_path` folders that the trust covers as well (F-7). */
  moduleFiles: string[];
  /** A fixed issue code of the server when the layout cannot be trusted (`state: 'invalid'`). */
  issue: string | null;
}

export interface SqliteVersionStatus {
  format: 'sqlite_user_version';
  current: number | null;
}

export interface AlembicStatus {
  format: 'alembic';
  codeTrust: CodeTrustState;
  scriptLocation: string | null;
  /** The ids in `alembic_version`; null when the table does not exist. */
  current: string[] | null;
  /** Read only with trusted code; null otherwise. */
  heads: string[] | null;
  pending: string[] | null;
  isCurrent: boolean | null;
  unknown: string[] | null;
  /** `read`, or why the revisions are unknown (the server's fixed text). */
  revisions: string;
  problem: Problem | null;
}

/** The Levuvo platforms' own version tables (STU-024): read only, nothing runs. */
export type LevuvoFormat = 'nodestro' | 'axentra' | 'orkexa';
export type LevuvoState = 'current' | 'pending' | 'newer' | 'changed' | 'no_files' | 'unreadable';

export interface LevuvoStep {
  version: number | null;
  name: string | null;
  status: string;
}

export interface LevuvoStatus {
  format: LevuvoFormat;
  /** `schema.table` of the version table; null when the database has none (only files were found). */
  table: string | null;
  namespace: string | null;
  current: number | null;
  /** An unknown state reads as `unreadable` (fail closed). */
  state: LevuvoState;
  steps: LevuvoStep[];
  problem: Problem | null;
}

/** A format this page does not know yet; it is counted, never dropped silently (review C-1 of #32). */
export interface OtherFormatStatus {
  format: 'other';
  name: string;
}

export type FormatStatus = SqliteVersionStatus | AlembicStatus | LevuvoStatus | OtherFormatStatus;

export interface MigrationStatus {
  connectionId: string;
  engine: string;
  formats: FormatStatus[];
}

const LEVUVO_FORMATS: readonly LevuvoFormat[] = ['nodestro', 'axentra', 'orkexa'];
const LEVUVO_STATES: readonly LevuvoState[] = [
  'current',
  'pending',
  'newer',
  'changed',
  'no_files',
  'unreadable',
];

function integer(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) ? value : null;
}

function parseLevuvo(format: LevuvoFormat, entry: Record<string, unknown>): LevuvoStatus {
  const schema = text(entry.schema);
  const table = text(entry.table);
  const steps = Array.isArray(entry.steps) ? entry.steps : [];
  return {
    format,
    table: table === null ? null : schema === null ? table : `${schema}.${table}`,
    namespace: text(entry.namespace),
    current: integer(entry.current),
    state: LEVUVO_STATES.includes(entry.state as LevuvoState)
      ? (entry.state as LevuvoState)
      : 'unreadable',
    steps: steps.flatMap((item: unknown): LevuvoStep[] => {
      if (typeof item !== 'object' || item === null) return [];
      const step = item as Record<string, unknown>;
      return [
        {
          version: integer(step.version),
          name: text(step.name),
          status: text(step.status) ?? 'unknown',
        },
      ];
    }),
    problem: parseProblem(entry.problem),
  };
}

const STATES: readonly CodeTrustState[] = ['none', 'untrusted', 'changed', 'trusted', 'invalid'];

function state(value: unknown): CodeTrustState {
  return STATES.includes(value as CodeTrustState) ? (value as CodeTrustState) : 'invalid';
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function texts(value: unknown): string[] | null {
  if (typeof value === 'string') return [value];
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : null;
}

/** The trust state, or null for an answer without a known state (the page then says it could not read it). */
export function parseCodeTrust(body: unknown): CodeTrust | null {
  if (typeof body !== 'object' || body === null) return null;
  const doc = body as Record<string, unknown>;
  if (!STATES.includes(doc.state as CodeTrustState)) return null;
  return {
    state: state(doc.state),
    configFile: text(doc.config_file),
    digest: text(doc.digest),
    scriptLocation: text(doc.script_location),
    revisionFiles: texts(doc.revision_files) ?? [],
    moduleFiles: texts(doc.module_files) ?? [],
    issue: text(doc.issue),
  };
}

function parseProblem(value: unknown): Problem | null {
  if (typeof value !== 'object' || value === null) return null;
  const doc = value as Record<string, unknown>;
  return {
    code: typeof doc.code === 'string' ? doc.code : 'studio.unknown',
    kind: typeof doc.kind === 'string' ? doc.kind : 'internal',
    status: typeof doc.status === 'number' ? doc.status : 0,
    logId: null,
  };
}

export function parseMigrations(body: unknown): MigrationStatus | null {
  if (typeof body !== 'object' || body === null) return null;
  const doc = body as Record<string, unknown>;
  const formats = Array.isArray(doc.formats) ? doc.formats : [];
  return {
    connectionId: text(doc.connection_id) ?? '',
    engine: text(doc.engine) ?? '',
    formats: formats.flatMap((item: unknown): FormatStatus[] => {
      if (typeof item !== 'object' || item === null) return [];
      const entry = item as Record<string, unknown>;
      if (entry.format === 'sqlite_user_version') {
        return [
          {
            format: 'sqlite_user_version',
            current: typeof entry.current === 'number' ? entry.current : null,
          },
        ];
      }
      if (LEVUVO_FORMATS.includes(entry.format as LevuvoFormat)) {
        return [parseLevuvo(entry.format as LevuvoFormat, entry)];
      }
      if (entry.format !== 'alembic') {
        return [{ format: 'other', name: text(entry.format) ?? '' }];
      }
      return [
        {
          format: 'alembic',
          codeTrust: state(entry.code_trust),
          scriptLocation: text(entry.script_location),
          current: texts(entry.current),
          heads: texts(entry.heads),
          pending: texts(entry.pending),
          isCurrent: typeof entry.is_current === 'boolean' ? entry.is_current : null,
          unknown: texts(entry.unknown),
          revisions: text(entry.revisions) ?? '',
          problem: parseProblem(entry.problem),
        },
      ];
    }),
  };
}

export async function fetchCodeTrust(fetcher: typeof fetch): Promise<CodeTrust | null> {
  return parseCodeTrust(await getJson(fetcher, '/api/project/code-trust'));
}

/** Trust the files with `digest` (the one the dialog showed). */
export function trustCode(fetcher: typeof fetch, digest: string): Promise<Result<null>> {
  return send<null>(fetcher, 'POST', '/api/project/code-trust', { digest });
}

export function revokeCodeTrust(fetcher: typeof fetch): Promise<Result<null>> {
  return send<null>(fetcher, 'DELETE', '/api/project/code-trust', {});
}

/**
 * Open (or get back) the read-only connection of a profile; its id. `confirmedEnvironment`: the profile's environment
 * as the page showed it, after D-02 for PROD (see `connectBody` in `./connections`; required, C-2 of the #48 review: a
 * PROD profile confirmed for anything else is refused with `studio.prod_confirmation_required`).
 */
export async function openConnection(
  fetcher: typeof fetch,
  profileId: string,
  confirmedEnvironment: EnvironmentTag,
): Promise<Result<string>> {
  const result = await send<{ connection_id?: unknown }>(
    fetcher,
    'POST',
    '/api/connections',
    connectBody(profileId, confirmedEnvironment),
  );
  if (!result.ok) return result;
  const id = result.value.connection_id;
  return typeof id === 'string' ? { ok: true, value: id } : { ok: false, problem: OFFLINE };
}

export async function fetchMigrations(
  fetcher: typeof fetch,
  connectionId: string,
): Promise<Result<MigrationStatus>> {
  try {
    const response = await fetcher(
      `/api/connections/${encodeURIComponent(connectionId)}/migrations`,
      {
        cache: 'no-store',
        credentials: 'same-origin',
        headers: apiHeaders(),
      },
    );
    if (!response.ok) return { ok: false, problem: await readProblem(response) };
    const status = parseMigrations(await response.json());
    return status === null ? { ok: false, problem: OFFLINE } : { ok: true, value: status };
  } catch {
    return { ok: false, problem: OFFLINE };
  }
}
