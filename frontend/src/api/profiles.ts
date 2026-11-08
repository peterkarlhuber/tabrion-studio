/**
 * Connection profiles and settings (STU-007, STU-008; docs/architecture/configuration.md).
 *
 * `GET /api/profiles` lists the user's and the trusted project's profiles. Studio writes only the user's own
 * `profiles.toml` and `settings.toml`: `POST`, `PUT` and `DELETE /api/profiles` (the id in the body) and
 * `PUT /api/settings`, each with the digest of the file the page read (`studio.config_stale` otherwise). A password is
 * sent once to `PUT /api/profiles/{id}/secret` and never comes back.
 */
import { apiHeaders } from '../session';
import { OFFLINE, readProblem, type Problem } from './problem';

export type Engine = 'sqlite' | 'postgresql';
export type EnvironmentTag = 'dev' | 'test' | 'prod';
export type SecretSource = 'keyring' | 'env' | 'file';

export interface ProfileFields {
  name: string;
  engine: Engine;
  environment: EnvironmentTag;
  path?: string;
  host?: string;
  port?: number;
  database?: string;
  user?: string;
  sslmode?: string;
  read_only_role?: boolean;
  statement_timeout_ms?: number;
  /** In the form: the reference as the file writes it (`keyring:`, `env:NAME`, `file:PATH`). */
  password?: string | null;
}

export interface ProfileItem extends Omit<ProfileFields, 'password'> {
  id: string;
  source: 'user' | 'project';
  password: { source: SecretSource; target: string } | null;
  /** `hasSecret`: true or false when known without reading the keyring or a file, else null. */
  secret: { source: SecretSource | null; hasSecret: boolean | null };
}

export interface FileState {
  digest: string | null;
  handWritten: boolean;
}

export interface ProfileList {
  profiles: ProfileItem[];
  projectState: string;
  userFile: FileState;
  issues: { pointer: string; code: string; severity: string }[];
}

const ENGINES: Engine[] = ['sqlite', 'postgresql'];
const ENVIRONMENTS: EnvironmentTag[] = ['dev', 'test', 'prod'];
const SOURCES: SecretSource[] = ['keyring', 'env', 'file'];

function text(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

function fileState(value: unknown): FileState {
  const doc = (typeof value === 'object' && value !== null ? value : {}) as Record<string, unknown>;
  return {
    digest: typeof doc.digest === 'string' ? doc.digest : null,
    handWritten: doc.hand_written === true,
  };
}

function profile(value: unknown): ProfileItem[] {
  if (typeof value !== 'object' || value === null) return [];
  const p = value as Record<string, unknown>;
  const engine = ENGINES.find((e) => e === p.engine);
  const environment = ENVIRONMENTS.find((e) => e === p.environment);
  if (typeof p.id !== 'string' || typeof p.name !== 'string' || !engine || !environment) return [];
  const password = (p.password ?? null) as Record<string, unknown> | null;
  const secret = (p.secret ?? {}) as Record<string, unknown>;
  const passwordSource = SOURCES.find((s) => s === password?.source);
  return [
    {
      id: p.id,
      source: p.source === 'project' ? 'project' : 'user',
      name: p.name,
      engine,
      environment,
      path: text(p.path),
      host: text(p.host),
      port: typeof p.port === 'number' ? p.port : undefined,
      database: text(p.database),
      user: text(p.user),
      sslmode: text(p.sslmode),
      read_only_role: typeof p.read_only_role === 'boolean' ? p.read_only_role : undefined,
      statement_timeout_ms:
        typeof p.statement_timeout_ms === 'number' ? p.statement_timeout_ms : undefined,
      password:
        passwordSource && password !== null
          ? { source: passwordSource, target: String(password.target ?? '') }
          : null,
      secret: {
        source: SOURCES.find((s) => s === secret.source) ?? null,
        hasSecret: typeof secret.has_secret === 'boolean' ? secret.has_secret : null,
      },
    },
  ];
}

/** Parse the answer defensively: anything unexpected becomes "nothing to show", never an exception. */
export function parseProfiles(body: unknown): ProfileList | null {
  if (typeof body !== 'object' || body === null) return null;
  const doc = body as Record<string, unknown>;
  if (!Array.isArray(doc.profiles)) return null;
  const issues = (doc.issues ?? {}) as Record<string, unknown>;
  return {
    profiles: doc.profiles.flatMap(profile),
    projectState: typeof doc.project_state === 'string' ? doc.project_state : 'none',
    userFile: fileState(doc.user_file),
    issues: Array.isArray(issues.user) ? (issues.user as ProfileList['issues']) : [],
  };
}

/** The reference of a listed profile as the form edits it (`env:NAME`). */
export function passwordReference(item: ProfileItem): string | null {
  if (item.password === null) return null;
  return item.password.source === 'keyring'
    ? 'keyring:'
    : `${item.password.source}:${item.password.target}`;
}

/** A listed profile as the form edits it. */
export function profileFields(item: ProfileItem): ProfileFields {
  const fields: ProfileFields = {
    name: item.name,
    engine: item.engine,
    environment: item.environment,
    password: passwordReference(item),
  };
  for (const key of [
    'path',
    'host',
    'port',
    'database',
    'user',
    'sslmode',
    'read_only_role',
    'statement_timeout_ms',
  ] as const) {
    if (item[key] !== undefined) (fields as unknown as Record<string, unknown>)[key] = item[key];
  }
  return fields;
}

async function getJson(fetcher: typeof fetch, path: string): Promise<unknown> {
  try {
    const response = await fetcher(path, {
      cache: 'no-store',
      credentials: 'same-origin',
      headers: apiHeaders(),
    });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

export async function fetchProfiles(fetcher: typeof fetch): Promise<ProfileList | null> {
  return parseProfiles(await getJson(fetcher, '/api/profiles'));
}

export async function fetchKeyringAvailable(fetcher: typeof fetch): Promise<boolean> {
  const body = (await getJson(fetcher, '/api/secrets')) as { keyring?: { available?: unknown } };
  return body?.keyring?.available === true;
}

export type Result<T> = { ok: true; value: T } | { ok: false; problem: Problem };

/**
 * A write to the API. Every one sends `Content-Type: application/json` with a JSON body, DELETE included: the access
 * middleware refuses an unsafe request without it (review of STU-007).
 */
async function send<T>(
  fetcher: typeof fetch,
  method: 'POST' | 'PUT' | 'DELETE',
  path: string,
  body: unknown,
): Promise<Result<T>> {
  try {
    const response = await fetcher(path, {
      method,
      cache: 'no-store',
      credentials: 'same-origin',
      headers: { ...apiHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) return { ok: false, problem: await readProblem(response) };
    const value = (response.status === 204 ? null : await response.json()) as T;
    return { ok: true, value };
  } catch {
    return { ok: false, problem: OFFLINE };
  }
}

export interface Saved {
  id?: string;
  digest: string;
}

export function createProfile(
  fetcher: typeof fetch,
  profile: ProfileFields,
  digest: string | null,
  overwrite = false,
): Promise<Result<Saved>> {
  return send(fetcher, 'POST', '/api/profiles', { profile, digest, overwrite });
}

export function updateProfile(
  fetcher: typeof fetch,
  id: string,
  profile: ProfileFields,
  digest: string | null,
  overwrite = false,
): Promise<Result<Saved>> {
  return send(fetcher, 'PUT', '/api/profiles', { id, profile, digest, overwrite });
}

export function deleteProfile(
  fetcher: typeof fetch,
  id: string,
  digest: string | null,
  overwrite = false,
): Promise<Result<Saved>> {
  return send(fetcher, 'DELETE', '/api/profiles', { id, digest, overwrite });
}

/** The id goes into the path as one segment: a name with `/`, `?` or `#` stays inside it. */
function secretPath(id: string): string {
  return `/api/profiles/${encodeURIComponent(id)}/secret`;
}

export function storeSecret(
  fetcher: typeof fetch,
  id: string,
  secret: string,
  store: 'keyring' | 'session',
): Promise<Result<null>> {
  return send(fetcher, 'PUT', secretPath(id), { secret, store });
}

export function forgetSecret(fetcher: typeof fetch, id: string): Promise<Result<null>> {
  return send(fetcher, 'DELETE', secretPath(id), {});
}

// --- settings.toml -----------------------------------------------------------------------------------------------

export interface ServerSettings {
  logSql: boolean;
  maxRows: number;
  statementTimeoutMs: number;
}

export interface SettingsState {
  settings: ServerSettings;
  file: FileState;
  issues: { pointer: string; code: string; severity: string }[];
}

export function parseSettings(body: unknown): ServerSettings | null {
  const doc = (typeof body === 'object' && body !== null ? body : {}) as Record<string, unknown>;
  const limits = (doc.limits ?? {}) as Record<string, unknown>;
  if (typeof limits.max_rows !== 'number' || typeof limits.statement_timeout_ms !== 'number') {
    return null;
  }
  return {
    logSql: doc.log_sql === true,
    maxRows: limits.max_rows,
    statementTimeoutMs: limits.statement_timeout_ms,
  };
}

export async function fetchSettings(fetcher: typeof fetch): Promise<SettingsState | null> {
  const body = (await getJson(fetcher, '/api/settings')) as Record<string, unknown> | null;
  const settings = parseSettings(body?.settings);
  if (body === null || settings === null) return null;
  return {
    settings,
    file: fileState(body.file),
    issues: Array.isArray(body.issues) ? (body.issues as SettingsState['issues']) : [],
  };
}

export function saveSettings(
  fetcher: typeof fetch,
  settings: ServerSettings,
  digest: string | null,
  overwrite = false,
): Promise<Result<{ digest: string; settings: unknown }>> {
  return send(fetcher, 'PUT', '/api/settings', {
    settings: {
      log_sql: settings.logSql,
      limits: { max_rows: settings.maxRows, statement_timeout_ms: settings.statementTimeoutMs },
    },
    digest,
    overwrite,
  });
}
