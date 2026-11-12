/**
 * Read-only database connections (STU-008 page, STU-009 routes; docs/architecture/connections.md).
 *
 * - `POST /api/connections {profile_id}` opens (or returns) the read-only connection of a profile. The backend checks
 *   it before it answers, so a wrong password or an unreachable host is the answer (a `db.<kind>` problem).
 * - `GET /api/connections` lists the open ones; `DELETE /api/connections {connection_id}` closes one.
 * - `POST /api/connection-tests {profile_id}` is the connection test: it connects, reads a few facts and closes again.
 *
 * Every answer is parsed defensively (TD-011: no generated API types yet): a field of the wrong type is "unknown",
 * never an exception. Names, versions and settings come from the database, so the page shows them escaped.
 */
import type { EnvironmentTag } from './profiles';
import { OFFLINE } from './problem';
import { getJson, send, type Result } from './profiles';

export interface OpenedConnection {
  connectionId: string;
  profileId: string | null;
  environment: EnvironmentTag | null;
}

/** What the connection test read besides the health check (03 section 2 A); null where Studio could not read it. */
export interface ConnectionFacts {
  role: string | null;
  superuser: boolean | null;
  bypassRls: boolean | null;
  encoding: string | null;
  timezone: string | null;
  tls: boolean | null;
  foreignKeys: boolean | null;
  journalMode: string | null;
}

export interface ConnectionTest {
  ok: boolean;
  engine: 'sqlite' | 'postgresql' | null;
  serverVersion: string | null;
  latencyMs: number | null;
  facts: ConnectionFacts;
}

const ENVIRONMENTS: EnvironmentTag[] = ['dev', 'test', 'prod'];

function record(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function flag(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

export function parseOpened(body: unknown): OpenedConnection | null {
  const doc = record(body);
  if (typeof doc.connection_id !== 'string' || doc.connection_id === '') return null;
  return {
    connectionId: doc.connection_id,
    profileId: text(doc.profile_id),
    environment: ENVIRONMENTS.find((e) => e === doc.environment) ?? null,
  };
}

export function parseTest(body: unknown): ConnectionTest | null {
  const doc = record(body);
  if (typeof doc.ok !== 'boolean') return null;
  const facts = record(doc.facts);
  const latency = doc.latency_ms;
  return {
    ok: doc.ok,
    engine: doc.engine === 'sqlite' || doc.engine === 'postgresql' ? doc.engine : null,
    serverVersion: text(doc.server_version),
    latencyMs: typeof latency === 'number' && Number.isFinite(latency) ? latency : null,
    facts: {
      role: text(facts.role),
      superuser: flag(facts.superuser),
      bypassRls: flag(facts.bypass_rls),
      encoding: text(facts.encoding),
      timezone: text(facts.timezone),
      tls: flag(facts.tls),
      foreignKeys: flag(facts.foreign_keys),
      journalMode: text(facts.journal_mode),
    },
  };
}

/** The ids of the open connections and their profiles; null when the list could not be read. */
export function parseOpenList(body: unknown): OpenedConnection[] | null {
  const doc = record(body);
  if (!Array.isArray(doc.connections)) return null;
  return doc.connections.flatMap((item: unknown) => {
    const opened = parseOpened(item);
    return opened === null ? [] : [opened];
  });
}

export async function openConnection(
  fetcher: typeof fetch,
  profileId: string,
): Promise<Result<OpenedConnection>> {
  const result = await send<unknown>(fetcher, 'POST', '/api/connections', {
    profile_id: profileId,
  });
  if (!result.ok) return result;
  const opened = parseOpened(result.value);
  return opened === null ? { ok: false, problem: OFFLINE } : { ok: true, value: opened };
}

export async function testConnection(
  fetcher: typeof fetch,
  profileId: string,
): Promise<Result<ConnectionTest>> {
  const result = await send<unknown>(fetcher, 'POST', '/api/connection-tests', {
    profile_id: profileId,
  });
  if (!result.ok) return result;
  const test = parseTest(result.value);
  return test === null ? { ok: false, problem: OFFLINE } : { ok: true, value: test };
}

export function closeConnection(
  fetcher: typeof fetch,
  connectionId: string,
): Promise<Result<null>> {
  return send<null>(fetcher, 'DELETE', '/api/connections', { connection_id: connectionId });
}

export async function fetchOpenConnections(
  fetcher: typeof fetch,
): Promise<OpenedConnection[] | null> {
  return parseOpenList(await getJson(fetcher, '/api/connections'));
}
