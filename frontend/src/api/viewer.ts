/**
 * The data viewer (STU-013, 03 sections 2 D, 5.3 and 6; backend: docs/architecture/viewer.md). Every route reads.
 *
 * - `GET /api/connections/{id}/rows?schema=&name=&limit=&sort=&desc=&filter=&after=`: one page of a table, view or
 *   materialized view, sorted and filtered; `next` is the token of the page after it. The query form always, so a
 *   name with `/` works too (I-6 of stu-010-review.md).
 * - `GET /api/connections/{id}/cell?schema=&name=&page=&row=&column=`: one value of a page in full (up to 10 MB).
 *
 * The parsers fail closed (like `api/explorer.ts`): a page whose rows do not have one value per column, whose cut
 * cells point outside the page, that answers for another connection, object, sort or limit than the one asked for,
 * or that leaves out one of these fields, is `studio.answer_invalid`, never shown. Every value is database content,
 * shown as plain text (threat T-07).
 *
 * The filters travel in the query string, and the server (Uvicorn on h11) refuses a request line and headers over
 * 16 KB with a plain 400 that names no problem. A rows request whose query string is longer than `MAX_QUERY_BYTES`
 * is therefore refused here, before it is sent, as `studio.viewer_filter_too_long` (the page's form says so first).
 */
import { ANSWER_INVALID, base, read } from './explorer';
import type { Result } from './profiles';
import type { QueryColumn } from './queries';

export type Paging = 'primary_key' | 'rowid' | 'ctid' | 'offset';
export type PagingHint = 'no_primary_key' | 'unstable_order';

export const FILTER_OPS = [
  'eq',
  'ne',
  'lt',
  'le',
  'gt',
  'ge',
  'contains',
  'starts_with',
  'is_null',
  'not_null',
] as const;
export type FilterOp = (typeof FILTER_OPS)[number];
/** The operators without a value. */
export const NO_VALUE_OPS: readonly FilterOp[] = ['is_null', 'not_null'];
/** The server takes at most 20 filters (all must hold). */
export const MAX_FILTERS = 20;
/** The server's page sizes: 1 to 1000 rows. */
export const MAX_LIMIT = 1000;
/**
 * The longest query string of a rows request, in bytes: below h11's 16 KB for the request line and headers, with room
 * for the path, a `next` token and the headers.
 */
export const MAX_QUERY_BYTES = 12 * 1024;
/** The longest `after` token the server takes (`max_length` of `after` in `api/viewer.py`). */
export const MAX_TOKEN_CHARS = 64;
/** A rows request whose query string would be longer than `MAX_QUERY_BYTES`: it is not sent. */
export const FILTER_TOO_LONG = 'studio.viewer_filter_too_long';

export interface ViewerFilter {
  column: string;
  op: FilterOp;
  /** Sent as text: the database compares it by the column's own rules (docs/architecture/viewer.md). */
  value: string | null;
}

export interface ViewerRequest {
  limit: number;
  sort: string | null;
  desc: boolean;
  filters: ViewerFilter[];
  /** The `next` token of the page before; null for the first page. */
  after: string | null;
}

export interface ViewerPage {
  columns: QueryColumn[];
  rows: unknown[][];
  /** `[row, column]` of every value the server cut (64 KB of text, 32 KB of bytes). */
  truncatedCells: [number, number][];
  /** The page stopped at the result size limit (`max_result_mb`) before `limit` rows. */
  cutByBytes: boolean;
  /** This page's token (for the cell detail). */
  page: string;
  /** The next page's token, or null on the last page. */
  next: string | null;
  paging: Paging;
  hint: PagingHint | null;
}

export interface ViewerCell {
  value: unknown;
  /** The whole value's size in bytes. */
  size: number;
  /** The value was cut at 10 MB. */
  truncated: boolean;
}

const PAGINGS: readonly Paging[] = ['primary_key', 'rowid', 'ctid', 'offset'];
const HINTS: readonly PagingHint[] = ['no_primary_key', 'unstable_order'];

type Doc = Record<string, unknown>;

function doc(value: unknown): Doc | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Doc)
    : null;
}

function index(value: unknown, below: number): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 && value < below;
}

/** A page as the server sent it, checked against what was asked for; null when it is not one. */
export function parsePage(
  body: unknown,
  asked: { schema: string; name: string; limit: number; sort: string | null; desc: boolean },
): ViewerPage | null {
  const page = doc(body);
  if (page === null) return null;
  // The server always echoes what it answered for (services/viewer.py); an answer without it is refused (I-2).
  if (page.schema !== asked.schema || page.name !== asked.name) return null;
  if (page.limit !== asked.limit || page.sort !== asked.sort || page.desc !== asked.desc)
    return null;
  if (!Array.isArray(page.columns) || !Array.isArray(page.rows)) return null;
  const columns: QueryColumn[] = [];
  for (const entry of page.columns) {
    const column = doc(entry);
    if (column === null || typeof column.name !== 'string') return null;
    columns.push({ name: column.name, type: typeof column.type === 'string' ? column.type : '' });
  }
  if (page.rows.length > asked.limit) return null;
  const rows: unknown[][] = [];
  for (const row of page.rows) {
    if (!Array.isArray(row) || row.length !== columns.length) return null;
    rows.push(row);
  }
  const cells: [number, number][] = [];
  for (const cell of Array.isArray(page.truncated_cells) ? page.truncated_cells : []) {
    if (!Array.isArray(cell) || !index(cell[0], rows.length) || !index(cell[1], columns.length)) {
      return null;
    }
    cells.push([cell[0], cell[1]]);
  }
  if (typeof page.page !== 'string' || page.page === '') return null;
  if (page.next !== null && page.next !== undefined && typeof page.next !== 'string') return null;
  const paging = PAGINGS.find((item) => item === page.paging);
  if (paging === undefined) return null;
  return {
    columns,
    rows,
    truncatedCells: cells,
    cutByBytes: page.truncated === 'bytes',
    page: page.page,
    next: typeof page.next === 'string' && page.next !== '' ? page.next : null,
    paging,
    hint: HINTS.find((item) => item === page.hint) ?? null,
  };
}

export function parseCell(body: unknown): ViewerCell | null {
  const cell = doc(body);
  if (cell === null || !('value' in cell)) return null;
  if (typeof cell.size !== 'number' || !Number.isSafeInteger(cell.size) || cell.size < 0) {
    return null;
  }
  if (typeof cell.truncated !== 'boolean') return null;
  return { value: cell.value, size: cell.size, truncated: cell.truncated };
}

/** The query string of a rows request (without `?`), as `fetchPage` sends it. */
export function pageQuery(schema: string, name: string, request: ViewerRequest): string {
  const query = new URLSearchParams({ schema, name, limit: String(request.limit) });
  if (request.sort !== null) query.set('sort', request.sort);
  if (request.desc) query.set('desc', 'true');
  if (request.filters.length > 0) {
    query.set(
      'filter',
      JSON.stringify(
        request.filters.map((filter) =>
          NO_VALUE_OPS.includes(filter.op)
            ? { column: filter.column, op: filter.op }
            : { column: filter.column, op: filter.op, value: filter.value },
        ),
      ),
    );
  }
  if (request.after !== null) query.set('after', request.after);
  return query.toString();
}

/** Whether a rows request's query string fits the server's request-head limit (it is ASCII once encoded). */
export function queryFits(schema: string, name: string, request: ViewerRequest): boolean {
  return pageQuery(schema, name, request).length <= MAX_QUERY_BYTES;
}

export async function fetchPage(
  fetcher: typeof fetch,
  connectionId: string,
  schema: string,
  name: string,
  request: ViewerRequest,
  signal?: AbortSignal,
): Promise<Result<ViewerPage>> {
  const query = pageQuery(schema, name, request);
  if (query.length > MAX_QUERY_BYTES) {
    return {
      ok: false,
      problem: { code: FILTER_TOO_LONG, kind: 'request', status: 0, logId: null },
    };
  }
  const asked = { schema, name, limit: request.limit, sort: request.sort, desc: request.desc };
  return read(
    fetcher,
    `${base(connectionId)}/rows?${query}`,
    (body) => {
      // The page of another connection is refused like the explorer's answers (I-1 of stu-010-review.md).
      if (doc(body)?.connection_id !== connectionId) return null;
      return parsePage(body, asked);
    },
    signal,
  );
}

export function fetchCell(
  fetcher: typeof fetch,
  connectionId: string,
  schema: string,
  name: string,
  page: string,
  row: number,
  column: string,
  signal?: AbortSignal,
): Promise<Result<ViewerCell>> {
  const query = new URLSearchParams({ schema, name, page, row: String(row), column });
  return read(fetcher, `${base(connectionId)}/cell?${query.toString()}`, parseCell, signal);
}

export { ANSWER_INVALID };
