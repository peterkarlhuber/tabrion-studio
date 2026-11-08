/**
 * Project trust (STU-006, 03 section 6): `GET /api/project` and `POST /api/project/trust`.
 *
 * The server decides what is trusted: it trusts the file only if the digest the dialog showed still matches the file
 * on disk. The page only shows the review and sends the digest back.
 */
import { apiHeaders } from '../session';
import { readProblem, type Problem } from './problem';

export type FlowWarning = 'remote_host' | 'weak_tls' | 'sensitive_variable' | 'secret_outside';

/** Where a PostgreSQL connection's password goes (T-5, T-4 of stu-006-review.md; L-1 of stu-007-review.md). */
export interface TrustFlow {
  /** `env:NAME`, `file:PATH`, `keyring:`, or null (asked when connecting). */
  secret: string | null;
  /** `host:port` */
  to: string;
  tls: string | null;
  warnings: FlowWarning[];
}

export interface TrustConnection {
  name: string;
  engine: string;
  environment: string;
  location: string;
  flow?: TrustFlow;
}

const WARNINGS: FlowWarning[] = ['remote_host', 'weak_tls', 'sensitive_variable', 'secret_outside'];

function flow(connection: Record<string, unknown>): TrustFlow | undefined {
  const raw = connection.flow as Record<string, unknown> | null | undefined;
  if (raw === null || typeof raw !== 'object' || typeof raw.to !== 'string') return undefined;
  const warnings = Array.isArray(connection.warnings) ? connection.warnings : [];
  return {
    secret: typeof raw.secret === 'string' ? raw.secret : null,
    to: raw.to,
    tls: typeof raw.tls === 'string' ? raw.tls : null,
    warnings: WARNINGS.filter((w) => warnings.includes(w)),
  };
}

export interface TrustFile {
  path: string;
  kind: 'database' | 'secret';
  outside: boolean;
}

export interface TrustReview {
  connections: TrustConnection[];
  hosts: string[];
  files: TrustFile[];
  env: string[];
  unknown_keys: string[];
}

export interface ListDiff<T> {
  added: T[];
  removed: T[];
}

export interface TrustDiff {
  connections: { added: string[]; removed: string[]; changed: string[] };
  hosts: ListDiff<string>;
  files: ListDiff<TrustFile>;
  env: ListDiff<string>;
  unknown_keys: ListDiff<string>;
}

export type TrustState = 'none' | 'untrusted' | 'changed' | 'trusted' | 'invalid';

export interface ProjectInfo {
  path: string;
  profilesFile: string;
  state: TrustState;
  digest: string | null;
  review: TrustReview | null;
  diff: TrustDiff | null;
  issues: { pointer: string; code: string; severity: string }[];
}

const STATES: TrustState[] = ['none', 'untrusted', 'changed', 'trusted', 'invalid'];

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

function files(value: unknown): TrustFile[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item: unknown) => {
    const f = item as Partial<TrustFile> | null;
    return f !== null && typeof f === 'object' && typeof f.path === 'string'
      ? [
          {
            path: f.path,
            kind: f.kind === 'secret' ? 'secret' : 'database',
            outside: f.outside !== false,
          },
        ]
      : [];
  });
}

function listDiff<T>(value: unknown, pick: (v: unknown) => T[]): ListDiff<T> {
  const d = (value ?? {}) as Record<string, unknown>;
  return { added: pick(d.added), removed: pick(d.removed) };
}

/** Parse the answer defensively: anything unexpected becomes "nothing to show", never an exception. */
export function parseProject(body: unknown): ProjectInfo | null {
  if (typeof body !== 'object' || body === null) return null;
  const doc = body as Record<string, unknown>;
  const state = STATES.find((s) => s === doc.state);
  if (state === undefined) return null;
  const raw = (doc.review ?? null) as Record<string, unknown> | null;
  const review: TrustReview | null =
    raw === null || typeof raw !== 'object'
      ? null
      : {
          connections: Array.isArray(raw.connections)
            ? raw.connections.flatMap((item: unknown) => {
                const c = item as Partial<TrustConnection> | null;
                return c !== null && typeof c === 'object' && typeof c.name === 'string'
                  ? [
                      {
                        name: c.name,
                        engine: String(c.engine ?? ''),
                        environment: String(c.environment ?? ''),
                        location: String(c.location ?? ''),
                        flow: flow(c as Record<string, unknown>),
                      },
                    ]
                  : [];
              })
            : [],
          hosts: strings(raw.hosts),
          files: files(raw.files),
          env: strings(raw.env),
          unknown_keys: strings(raw.unknown_keys),
        };
  const d = (doc.diff ?? null) as Record<string, unknown> | null;
  const diff: TrustDiff | null =
    d === null || typeof d !== 'object'
      ? null
      : {
          connections: {
            ...listDiff(d.connections, strings),
            changed: strings((d.connections as Record<string, unknown> | undefined)?.changed),
          },
          hosts: listDiff(d.hosts, strings),
          files: listDiff(d.files, files),
          env: listDiff(d.env, strings),
          unknown_keys: listDiff(d.unknown_keys, strings),
        };
  return {
    path: typeof doc.path === 'string' ? doc.path : '',
    profilesFile:
      typeof doc.profiles_file === 'string' ? doc.profiles_file : '.tabrion/studio.toml',
    state,
    digest: typeof doc.digest === 'string' ? doc.digest : null,
    review,
    diff,
    issues: Array.isArray(doc.issues) ? (doc.issues as ProjectInfo['issues']) : [],
  };
}

export async function fetchProject(fetcher: typeof fetch): Promise<ProjectInfo | null> {
  try {
    const response = await fetcher('/api/project', {
      cache: 'no-store',
      credentials: 'same-origin',
      headers: apiHeaders(),
    });
    if (!response.ok) return null;
    return parseProject(await response.json());
  } catch {
    return null;
  }
}

/** Trust the file with `digest`; null on success, else the server's problem. */
export async function trustProject(fetcher: typeof fetch, digest: string): Promise<Problem | null> {
  try {
    const response = await fetcher('/api/project/trust', {
      method: 'POST',
      cache: 'no-store',
      credentials: 'same-origin',
      headers: { ...apiHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ digest }),
    });
    return response.ok ? null : await readProblem(response);
  } catch {
    return { code: 'studio.offline', kind: 'internal', status: 0, logId: null };
  }
}
