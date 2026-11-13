/**
 * TD-034, 08 section 5 "error recovery": which open connection a request used, and whether the answer says that the
 * connection is lost. Every view reads through the fetcher the page gives it, so one wrapper sees every answer:
 *
 * - a request on an open connection (`/api/connections/{id}/…`, or `POST /api/queries` and `POST /api/exports` with
 *   `connection_id` in the body) answered with `db.connection` (the database does not answer), or
 *   `studio.connection_not_found` / `studio.connection_stale` (the server closed it) reports `lost`;
 * - `POST /api/connections` answered with a connection reports `opened` (how "Reconnect" ends the banner);
 * - `GET /api/connections` reports the open connections as `listed` (the connection manager may take one over).
 *
 * The wrapper reads a copy of the answer (`Response.clone()`), never the caller's body, and never changes an answer.
 * `DELETE /api/connections` (the user's own "Disconnect") and the connection test are not watched.
 */
import { parseOpened, parseOpenList, type OpenedConnection } from './api/connections';
import { readProblem } from './api/problem';

/** The problem codes that mean the connection a request used is gone. */
export const LOST_CODES: ReadonlySet<string> = new Set([
  'db.connection',
  'studio.connection_not_found',
  'studio.connection_stale',
]);

export interface ConnectionWatchHooks {
  lost: (connectionId: string, code: string) => void;
  opened: (connection: OpenedConnection) => void;
  listed: (connections: OpenedConnection[]) => void;
}

const ON_CONNECTION = /^\/api\/connections\/([^/?#]+)\/[^?#]/;
const WITH_BODY = new Set(['/api/queries', '/api/exports']);

function requestPath(input: RequestInfo | URL): string {
  const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
  try {
    return new URL(raw, 'http://studio.invalid').pathname;
  } catch {
    return '';
  }
}

/** The open connection a request used, or null. */
export function requestConnection(
  input: RequestInfo | URL,
  init: RequestInit | undefined,
): string | null {
  const path = requestPath(input);
  const match = ON_CONNECTION.exec(path);
  if (match?.[1] !== undefined) {
    try {
      return decodeURIComponent(match[1]);
    } catch {
      return null;
    }
  }
  if ((init?.method ?? 'GET').toUpperCase() !== 'POST' || !WITH_BODY.has(path)) return null;
  if (typeof init?.body !== 'string') return null;
  try {
    const doc = JSON.parse(init.body) as { connection_id?: unknown } | null;
    return typeof doc?.connection_id === 'string' ? doc.connection_id : null;
  } catch {
    return null;
  }
}

async function inspect(
  input: RequestInfo | URL,
  init: RequestInit | undefined,
  response: Response,
  hooks: ConnectionWatchHooks,
): Promise<void> {
  if (typeof response.clone !== 'function') return;
  const method = (init?.method ?? 'GET').toUpperCase();
  const path = requestPath(input);
  if (path === '/api/connections' && (method === 'POST' || method === 'GET')) {
    if (!response.ok) return;
    const body: unknown = await response.clone().json();
    if (method === 'POST') {
      const opened = parseOpened(body);
      if (opened !== null) hooks.opened(opened);
    } else {
      const listed = parseOpenList(body);
      if (listed !== null) hooks.listed(listed);
    }
    return;
  }
  if (response.ok) return;
  const connectionId = requestConnection(input, init);
  if (connectionId === null) return;
  const problem = await readProblem(response.clone());
  if (LOST_CODES.has(problem.code)) hooks.lost(connectionId, problem.code);
}

/** `fetcher`, reporting lost and opened connections to `hooks`; the answers are passed on unchanged. */
export function watchConnections(fetcher: typeof fetch, hooks: ConnectionWatchHooks): typeof fetch {
  return async (input, init) => {
    const response = await fetcher(input, init);
    try {
      await inspect(input, init, response, hooks);
    } catch {
      // A body that is not JSON says nothing about the connection.
    }
    return response;
  };
}
