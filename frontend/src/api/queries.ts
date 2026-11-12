/**
 * The query engine (STU-011, docs/architecture/queries.md) as the SQL editor uses it (STU-012).
 *
 * - `POST /api/queries {connection_id, sql, params, max_rows}`: 202 with the state; one statement, read-only. The page
 *   never sends `mode`: Studio 1.0 does not write (the Data Editor, STU-026, stays locked).
 * - `GET /api/queries/{id}/events`: server-sent events, read with `fetch` because the CSRF header must be sent
 *   (`EventSource` cannot send headers). `followQuery` falls back to `GET /api/queries/{id}` when the stream breaks.
 * - `GET /api/queries/{id}/rows?offset&limit`: a page of at most 5 000 rows. The page asks only for what is visible
 *   (`rowCache.ts`): a result is never loaded into the browser as a whole (03 section 6).
 * - `POST /api/queries/{id}/cancel`.
 *
 * Every value from the database is data: the grid renders it as text, never as HTML (threat T-07).
 */
import { apiHeaders } from '../session';
import { OFFLINE, readProblem, type Problem } from './problem';
import { send, type Result } from './profiles';

export type QueryState = 'running' | 'done' | 'failed' | 'canceled';
export type ParamValue = string | number | boolean | null;

export interface QueryColumn {
  name: string;
  /** The PostgreSQL type name (`int4`, `jsonb`, `int4[]`); empty for SQLite. */
  type: string;
}

export interface QueryStatus {
  queryId: string;
  state: QueryState;
  rowCount: number;
  maxRows: number;
  truncatedBy: 'rows' | 'bytes' | null;
  elapsedMs: number;
  columns: QueryColumn[];
  problem: Problem | null;
}

export interface RowPage {
  offset: number;
  rows: unknown[][];
  /** `[row, column]` of cells the server cut, the row relative to `offset`. */
  truncatedCells: [number, number][];
}

const STATES: readonly QueryState[] = ['running', 'done', 'failed', 'canceled'];
export const FINAL_STATES: readonly QueryState[] = ['done', 'failed', 'canceled'];
/** The page size the grid asks for (the server allows up to 5 000). */
export const PAGE_ROWS = 500;

function count(value: unknown): number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : 0;
}

function problem(value: unknown, status: number): Problem | null {
  if (typeof value !== 'object' || value === null) return null;
  const doc = value as Record<string, unknown>;
  return {
    code: typeof doc.code === 'string' ? doc.code : 'studio.unknown',
    kind: typeof doc.kind === 'string' ? doc.kind : 'internal',
    status: typeof doc.status === 'number' ? doc.status : status,
    logId: typeof doc.log_id === 'string' && /^[0-9a-f]{12}$/.test(doc.log_id) ? doc.log_id : null,
  };
}

/** The state document of the server, or null when it is not one. An unknown state reads as failed (fail closed). */
export function parseStatus(body: unknown): QueryStatus | null {
  if (typeof body !== 'object' || body === null) return null;
  const doc = body as Record<string, unknown>;
  if (typeof doc.query_id !== 'string') return null;
  const state = STATES.includes(doc.state as QueryState) ? (doc.state as QueryState) : 'failed';
  const columns = Array.isArray(doc.columns) ? doc.columns : [];
  return {
    queryId: doc.query_id,
    state,
    rowCount: count(doc.row_count),
    maxRows: count(doc.max_rows),
    truncatedBy:
      doc.truncated_by === 'rows' || doc.truncated_by === 'bytes' ? doc.truncated_by : null,
    elapsedMs: count(doc.elapsed_ms),
    columns: columns.map((item: unknown): QueryColumn => {
      const column = (typeof item === 'object' && item !== null ? item : {}) as Record<
        string,
        unknown
      >;
      return {
        name: typeof column.name === 'string' ? column.name : '',
        type: typeof column.type === 'string' ? column.type : '',
      };
    }),
    problem:
      problem(doc.problem, 0) ??
      (state === 'failed' && !STATES.includes(doc.state as QueryState)
        ? { code: 'studio.unknown', kind: 'internal', status: 0, logId: null }
        : null),
  };
}

export function parseRows(body: unknown): RowPage | null {
  if (typeof body !== 'object' || body === null) return null;
  const doc = body as Record<string, unknown>;
  if (!Array.isArray(doc.rows)) return null;
  const cells = Array.isArray(doc.truncated_cells) ? doc.truncated_cells : [];
  return {
    offset: count(doc.offset),
    rows: doc.rows.map((row: unknown) => (Array.isArray(row) ? row : [])),
    truncatedCells: cells.flatMap((cell: unknown): [number, number][] =>
      Array.isArray(cell) && typeof cell[0] === 'number' && typeof cell[1] === 'number'
        ? [[cell[0], cell[1]]]
        : [],
    ),
  };
}

export interface QueryRequest {
  connectionId: string;
  sql: string;
  params: Record<string, ParamValue>;
  maxRows?: number;
}

export async function startQuery(
  fetcher: typeof fetch,
  request: QueryRequest,
): Promise<Result<QueryStatus>> {
  const body: Record<string, unknown> = {
    connection_id: request.connectionId,
    sql: request.sql,
    params: request.params,
  };
  if (request.maxRows !== undefined) body.max_rows = request.maxRows;
  const result = await send<unknown>(fetcher, 'POST', '/api/queries', body);
  if (!result.ok) return result;
  const status = parseStatus(result.value);
  return status === null ? { ok: false, problem: OFFLINE } : { ok: true, value: status };
}

export async function cancelQuery(fetcher: typeof fetch, queryId: string): Promise<Result<null>> {
  const result = await send<unknown>(
    fetcher,
    'POST',
    `/api/queries/${encodeURIComponent(queryId)}/cancel`,
    {},
  );
  return result.ok ? { ok: true, value: null } : result;
}

async function get(fetcher: typeof fetch, path: string, signal?: AbortSignal): Promise<Response> {
  return fetcher(path, {
    cache: 'no-store',
    credentials: 'same-origin',
    headers: apiHeaders(),
    signal,
  });
}

export async function fetchStatus(
  fetcher: typeof fetch,
  queryId: string,
  signal?: AbortSignal,
): Promise<Result<QueryStatus>> {
  try {
    const response = await get(fetcher, `/api/queries/${encodeURIComponent(queryId)}`, signal);
    if (!response.ok) return { ok: false, problem: await readProblem(response) };
    const status = parseStatus(await response.json());
    return status === null ? { ok: false, problem: OFFLINE } : { ok: true, value: status };
  } catch {
    return { ok: false, problem: OFFLINE };
  }
}

export async function fetchRows(
  fetcher: typeof fetch,
  queryId: string,
  offset: number,
  limit: number = PAGE_ROWS,
  signal?: AbortSignal,
): Promise<Result<RowPage>> {
  try {
    const response = await get(
      fetcher,
      `/api/queries/${encodeURIComponent(queryId)}/rows?offset=${offset}&limit=${limit}`,
      signal,
    );
    if (!response.ok) return { ok: false, problem: await readProblem(response) };
    const page = parseRows(await response.json());
    return page === null ? { ok: false, problem: OFFLINE } : { ok: true, value: page };
  } catch {
    return { ok: false, problem: OFFLINE };
  }
}

/** The events of a `text/event-stream` body: `{event, data}` per block, comments (keep-alive) skipped. */
export async function* readEvents(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<{ event: string; data: string }> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n?/g, '\n');
      let end = buffer.indexOf('\n\n');
      while (end !== -1) {
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        let event = 'message';
        const data: string[] = [];
        for (const line of block.split('\n')) {
          if (line.startsWith(':')) continue;
          const colon = line.indexOf(':');
          const field = colon === -1 ? line : line.slice(0, colon);
          const text = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '');
          if (field === 'event') event = text;
          else if (field === 'data') data.push(text);
        }
        if (data.length > 0) yield { event, data: data.join('\n') };
        end = buffer.indexOf('\n\n');
      }
      if (done) return;
    }
  } finally {
    reader.releaseLock();
  }
}

const POLL_MS = 250;

function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

/**
 * Follow a query until it ends: `onState` for every state the server sends, the final one last. Reads the event
 * stream; when it breaks or is not available, asks for the state every 250 ms. Resolves with the final state, or
 * with a problem (the query is unknown, the server is gone, or `signal` was aborted).
 */
export async function followQuery(
  fetcher: typeof fetch,
  queryId: string,
  onState: (status: QueryStatus) => void,
  signal: AbortSignal,
): Promise<Result<QueryStatus>> {
  try {
    const response = await get(
      fetcher,
      `/api/queries/${encodeURIComponent(queryId)}/events`,
      signal,
    );
    if (response.status === 404) return { ok: false, problem: await readProblem(response) };
    if (response.ok && response.body !== null) {
      for await (const { data } of readEvents(response.body)) {
        let status: QueryStatus | null = null;
        try {
          status = parseStatus(JSON.parse(data));
        } catch {
          status = null;
        }
        if (status === null) continue;
        onState(status);
        if (FINAL_STATES.includes(status.state)) return { ok: true, value: status };
      }
    }
  } catch {
    // The stream broke: ask for the state instead.
  }
  let offline = 0;
  while (!signal.aborted) {
    const result = await fetchStatus(fetcher, queryId, signal);
    if (result.ok) {
      offline = 0;
      onState(result.value);
      if (FINAL_STATES.includes(result.value.state)) return result;
    } else if (result.problem.code !== OFFLINE.code || ++offline >= 20) {
      return result;
    }
    await wait(POLL_MS, signal);
  }
  return { ok: false, problem: OFFLINE };
}
