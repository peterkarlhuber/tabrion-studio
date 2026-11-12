/**
 * The data viewer (STU-013, 03 sections 2 D, 5.3 and 6; backend: docs/architecture/viewer.md). Every route reads.
 *
 * - `GET /api/connections/{id}/rows?schema=&name=&limit=&sort=&desc=&filter=&after=`: one page of a table, view or
 *   materialized view, sorted and filtered; `next` is the token of the page after it. The query form always, so a
 *   name with `/` works too (I-6 of stu-010-review.md).
 * - `GET /api/connections/{id}/cell?schema=&name=&page=&row=&column=`: one value of a page in full (up to 10 MB).
 *
 * The parsers fail closed (like `api/explorer.ts`): a page whose rows do not have one value per column, whose cut
 * cells point outside the page, or that answers for another object, sort or limit than the one asked for is
 * `studio.answer_invalid`, never shown. Every value is database content, shown as plain text (threat T-07).
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
  if (page.schema !== undefined && page.schema !== asked.schema) return null;
  if (page.name !== undefined && page.name !== asked.name) return null;
  if (page.limit !== undefined && page.limit !== asked.limit) return null;
  if (page.sort !== undefined && page.sort !== asked.sort) return null;
  if (page.desc !== undefined && page.desc !== asked.desc) return null;
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

export function fetchPage(
  fetcher: typeof fetch,
  connectionId: string,
  schema: string,
  name: string,
  request: ViewerRequest,
  signal?: AbortSignal,
): Promise<Result<ViewerPage>> {
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
  const asked = { schema, name, limit: request.limit, sort: request.sort, desc: request.desc };
  return read(
    fetcher,
    `${base(connectionId)}/rows?${query.toString()}`,
    (body) => {
      // The page of another connection is refused like the explorer's answers (I-1 of stu-010-review.md).
      const answer = doc(body);
      if (answer?.connection_id !== undefined && answer.connection_id !== connectionId) return null;
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
