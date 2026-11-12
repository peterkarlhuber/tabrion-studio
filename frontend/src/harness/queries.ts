/**
 * A fake query engine for the e2e harness and the jsdom tests of the SQL editor (STU-012). It answers the routes of
 * STU-011 (`POST /api/queries`, events, rows, cancel) the way the real engine does, from the statement's text:
 *
 * - `generate_series(1, N)` returns N rows (default 3) with an integer, a text, a JSON, a bytes and a NULL column; the
 *   row limit (`max_rows`, 1 000 by default) cuts the result with `truncated_by: "rows"`; row 2 has a cut cell.
 * - `pg_sleep` runs until it is canceled.
 * - a first word that is not a reading one (`INSERT`, `DELETE`, ...) is refused with `studio.statement_refused`;
 *   `missing_table` fails with `db.undefined_object`; `read_only_violation` with `db.read_only`.
 * - `:name` placeholders come back as the column `param` of every row.
 *
 * `requests` records what the page sent (no parameter value is ever logged by the real server; this is a test fake).
 */
import type { ParamValue } from '../api/queries';

interface FakeQuery {
  id: string;
  rowCount: number;
  maxRows: number;
  state: 'running' | 'done' | 'failed' | 'canceled';
  problem: { code: string; kind: string; status: number } | null;
  started: number;
  slow: boolean;
  param: ParamValue | undefined;
  listeners: Set<() => void>;
}

export interface QueryLog {
  starts: { sql: string; params: Record<string, ParamValue>; maxRows: number | null }[];
  rows: { offset: number; limit: number }[];
  cancels: string[];
  connections: string[];
}

const COLUMNS = [
  { name: 'n', type: 'int4' },
  { name: 'label', type: 'text' },
  { name: 'doc', type: 'jsonb' },
  { name: 'data', type: 'bytea' },
  { name: 'note', type: 'text' },
];

const READING = /^\s*(?:select|with|values|table|explain|show|pragma)\b/i;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function problem(code: string, status: number): Response {
  return json({ code, kind: code.startsWith('db.') ? 'database' : 'request', status }, status);
}

export function createQueryServer(): {
  handle: (url: string, init?: RequestInit) => Promise<Response> | null;
  log: QueryLog;
} {
  const queries = new Map<string, FakeQuery>();
  const log: QueryLog = { starts: [], rows: [], cancels: [], connections: [] };
  let next = 1;

  const status = (query: FakeQuery) => ({
    query_id: query.id,
    connection_id: 'c1',
    state: query.state,
    row_count: query.state === 'done' ? Math.min(query.rowCount, query.maxRows) : 0,
    max_rows: query.maxRows,
    truncated: query.state === 'done' && query.rowCount > query.maxRows,
    truncated_by: query.state === 'done' && query.rowCount > query.maxRows ? 'rows' : null,
    elapsed_ms: query.state === 'running' ? Date.now() - query.started : 24,
    columns:
      query.state === 'done'
        ? [...COLUMNS, ...(query.param === undefined ? [] : [{ name: 'param', type: '' }])]
        : [],
    ...(query.problem ? { problem: query.problem } : {}),
  });

  const row = (query: FakeQuery, index: number): unknown[] => {
    const n = index + 1;
    const values: unknown[] = [
      n,
      n === 2 ? `${'long text '.repeat(40)}end` : `row ${n}`,
      { n, tags: ['a', 'b'] },
      { base64: btoa(String.fromCharCode(0xde, 0xad, 0xbe, 0xef, n % 256)) },
      n % 3 === 0 ? null : `note ${n}`,
    ];
    if (query.param !== undefined) values.push(query.param);
    return values;
  };

  const finish = (query: FakeQuery, state: FakeQuery['state']) => {
    query.state = state;
    for (const listener of query.listeners) listener();
  };

  const events = (query: FakeQuery): Response => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        const send = () => {
          const doc = status(query);
          const name = query.state === 'running' ? 'state' : query.state;
          controller.enqueue(encoder.encode(`event: ${name}\ndata: ${JSON.stringify(doc)}\n\n`));
          if (query.state !== 'running') {
            query.listeners.delete(send);
            controller.close();
          }
        };
        controller.enqueue(encoder.encode(': keep-alive\n\n'));
        send();
        if (query.state === 'running') query.listeners.add(send);
      },
    });
    return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
  };

  const handle = (url: string, init?: RequestInit): Promise<Response> | null => {
    const method = init?.method ?? 'GET';
    if (url === '/api/connections' && method === 'POST') {
      const body = JSON.parse(String(init?.body ?? '{}')) as { profile_id?: string };
      log.connections.push(body.profile_id ?? '');
      return Promise.resolve(json({ connection_id: `c-${body.profile_id ?? ''}` }, 201));
    }
    if (url === '/api/queries' && method === 'POST') {
      const body = JSON.parse(String(init?.body ?? '{}')) as {
        sql: string;
        params?: Record<string, ParamValue>;
        max_rows?: number;
        mode?: string;
      };
      log.starts.push({ sql: body.sql, params: body.params ?? {}, maxRows: body.max_rows ?? null });
      if (body.mode !== undefined && body.mode !== 'read') {
        return Promise.resolve(problem('studio.query_write_unsupported', 403));
      }
      if (!READING.test(body.sql.replace(/^\s*(--[^\n]*\n|\/\*[\s\S]*?\*\/)*/g, ''))) {
        return Promise.resolve(problem('studio.statement_refused', 400));
      }
      const series = /generate_series\(\s*1\s*,\s*(\d+)\s*\)/i.exec(body.sql);
      const params = Object.values(body.params ?? {});
      const query: FakeQuery = {
        id: `q${next++}`,
        rowCount: series ? Number(series[1]) : 3,
        maxRows: body.max_rows ?? 1_000,
        state: 'running',
        problem: null,
        started: Date.now(),
        slow: /pg_sleep/i.test(body.sql),
        param: params.length > 0 ? params[0] : undefined,
        listeners: new Set(),
      };
      queries.set(query.id, query);
      const answer = json(status(query), 202);
      if (!query.slow) {
        setTimeout(() => {
          if (/missing_table/i.test(body.sql)) {
            query.problem = { code: 'db.undefined_object', kind: 'database', status: 404 };
            finish(query, 'failed');
          } else if (/read_only_violation/i.test(body.sql)) {
            query.problem = { code: 'db.read_only', kind: 'database', status: 403 };
            finish(query, 'failed');
          } else {
            finish(query, 'done');
          }
        }, 30);
      }
      return Promise.resolve(answer);
    }
    const match = /^\/api\/queries\/([^/?]+)(\/events|\/rows|\/cancel)?(?:\?(.*))?$/.exec(url);
    if (match === null) return null;
    const query = queries.get(decodeURIComponent(match[1] ?? ''));
    if (query === undefined) return Promise.resolve(problem('studio.query_not_found', 404));
    switch (match[2]) {
      case '/events':
        return Promise.resolve(events(query));
      case '/cancel':
        log.cancels.push(query.id);
        if (query.state === 'running') {
          setTimeout(() => {
            query.problem = { code: 'studio.query_canceled', kind: 'database', status: 409 };
            finish(query, 'canceled');
          }, 20);
        }
        return Promise.resolve(json(status(query), 202));
      case '/rows': {
        const search = new URLSearchParams(match[3] ?? '');
        const offset = Number(search.get('offset') ?? 0);
        const limit = Number(search.get('limit') ?? 1_000);
        log.rows.push({ offset, limit });
        const count = Math.min(query.rowCount, query.maxRows);
        const rows: unknown[][] = [];
        for (let i = offset; i < Math.min(count, offset + limit); i += 1) rows.push(row(query, i));
        const cut = offset <= 1 && 1 < offset + limit && count > 1 ? [[1 - offset, 1]] : [];
        return Promise.resolve(
          json({
            query_id: query.id,
            state: query.state,
            columns: status(query).columns,
            offset,
            rows,
            row_count: count,
            truncated: query.rowCount > query.maxRows,
            truncated_by: query.rowCount > query.maxRows ? 'rows' : null,
            truncated_cells: cut,
          }),
        );
      }
      default:
        return Promise.resolve(json(status(query)));
    }
  };
  return { handle, log };
}
