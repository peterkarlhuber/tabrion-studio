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
import { readProblem, OFFLINE, type Problem } from './problem';
import { getJson, send, type Result } from './profiles';
import { apiHeaders } from '../session';

export type CodeTrustState = 'none' | 'untrusted' | 'changed' | 'trusted' | 'invalid';

export interface CodeTrust {
  state: CodeTrustState;
  configFile: string | null;
  digest: string | null;
  scriptLocation: string | null;
  revisionFiles: string[];
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

export type FormatStatus = SqliteVersionStatus | AlembicStatus;

export interface MigrationStatus {
  connectionId: string;
  engine: string;
  formats: FormatStatus[];
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
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : null;
}

export function parseCodeTrust(body: unknown): CodeTrust | null {
  if (typeof body !== 'object' || body === null) return null;
  const doc = body as Record<string, unknown>;
  return {
    state: state(doc.state),
    configFile: text(doc.config_file),
    digest: text(doc.digest),
    scriptLocation: text(doc.script_location),
    revisionFiles: texts(doc.revision_files) ?? [],
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
      if (entry.format !== 'alembic') return [];
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

/** Open (or get back) the read-only connection of a profile; its id. */
export async function openConnection(fetcher: typeof fetch, profileId: string): Promise<Result<string>> {
  const result = await send<{ connection_id?: unknown }>(fetcher, 'POST', '/api/connections', {
    profile_id: profileId,
  });
  if (!result.ok) return result;
  const id = result.value.connection_id;
  return typeof id === 'string' ? { ok: true, value: id } : { ok: false, problem: OFFLINE };
}

export async function fetchMigrations(
  fetcher: typeof fetch,
  connectionId: string,
): Promise<Result<MigrationStatus>> {
  try {
    const response = await fetcher(`/api/connections/${encodeURIComponent(connectionId)}/migrations`, {
      cache: 'no-store',
      credentials: 'same-origin',
      headers: apiHeaders(),
    });
    if (!response.ok) return { ok: false, problem: await readProblem(response) };
    const status = parseMigrations(await response.json());
    return status === null ? { ok: false, problem: OFFLINE } : { ok: true, value: status };
  } catch {
    return { ok: false, problem: OFFLINE };
  }
}
