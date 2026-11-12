/**
 * One page of the data viewer in the browser (STU-013 page, 08 section 4.5), bounded in bytes like the editor's
 * results (`views/editor/rowCache.ts`, M-4 of stu-012-review.md).
 *
 * The viewer's pages arrive whole, so the bound is set before the request: `pageLimit` asks for no more rows than fit
 * in `MAX_BYTES` (64 MB, the editor's bound) if every value were as large as its column's declared type lets it be in
 * a page. The server cuts each value at 64 KB of text (65 536 characters, 128 KB as a JavaScript string) or 32 KB of
 * bytes; a JSON or array value arrives decoded, and its objects take far more memory than its text (R-1 of the
 * STU-013 re-review), so it counts 16 times; a column whose type holds only short values (numbers, dates, UUIDs,
 * `VARCHAR(n)`) counts by that size. A table of narrow columns gets the page size the user chose; a table of many
 * text columns gets fewer rows per page, and the page says so.
 *
 * SQLite does not enforce declared types (outside `STRICT` tables), so a column declared `INTEGER` may still hold a
 * long text. A walk (first page, then "Next") keeps the rows per page of its first page, as the server's `next` token
 * does; a walk started later (another sort, filter or page size, "Reload") also stays within the budget at the widest
 * row seen so far (`observedLimit`). Every page stays within the server's `max_result_mb` (docs/TODO.md TD-042).
 *
 * `PageRows` holds the rows of the page (and the in-page search, 03 section 2 D "full-text search in the current
 * page") behind the grid's `GridRows` interface.
 */
import type { Engine } from '../../api/explorer';
import { MAX_BYTES, rowBytes, type CachedRow, type GridRows } from '../editor/rowCache';
import { fullText } from '../editor/format';

/** The server's cap on one value of a page: 64 K characters of text (services/queries.py `CELL_CHARS`). */
export const CELL_CHARS = 64 * 1024;
/** The most a page of rows may take in the browser, estimated: the editor's bound. */
export const PAGE_BYTES = MAX_BYTES;
const VALUE_OVERHEAD = 16;
const ROW_OVERHEAD = 64;
/** A decoded JSON or array value takes this many times its text. */
const DECODED_FACTOR = 16;
/** Bytes of a short value (a number, a date, a UUID). */
const SHORT_BYTES = 96;
/** The page sizes offered (the server takes 1 to 1000). */
export const PAGE_SIZES = [50, 100, 250, 500, 1000] as const;
export const DEFAULT_PAGE_SIZE = 100;

const SHORT_TYPES =
  /^(SMALLINT|INTEGER|INT|INT2|INT4|INT8|BIGINT|SMALLSERIAL|SERIAL|BIGSERIAL|REAL|FLOAT4|FLOAT8|DOUBLE PRECISION|FLOAT(\(\d+\))?|BOOLEAN|BOOL|DATE|TIME(\(\d+\))?( WITH(OUT)? TIME ZONE)?|TIMESTAMP(\(\d+\))?( WITH(OUT)? TIME ZONE)?|TIMETZ|TIMESTAMPTZ|INTERVAL.*|UUID|OID|MONEY|INET|CIDR|MACADDR8?|CHAR|"CHAR")$/;
const SIZED_TEXT =
  /^(?:CHARACTER VARYING|VARCHAR|CHARACTER|CHAR|BPCHAR|BIT VARYING|VARBIT|BIT)\((\d+)\)$/;
const SIZED_NUMERIC = /^(?:NUMERIC|DECIMAL)\((\d+)(?:,\s*\d+)?\)$/;
const DECODED = /(\[\]$|^ARRAY|^JSONB?$)/;

/** The most memory one value of a column of this type can take in a page (estimated, in bytes). */
export function worstValueBytes(engine: Engine, type: string | null): number {
  const long = VALUE_OVERHEAD + 2 * CELL_CHARS;
  if (type === null) return long;
  const declared = type.trim().toUpperCase();
  // SQLite's affinities without a size: NUMERIC, DECIMAL and DATETIME hold numbers or short texts.
  if (engine === 'sqlite' && /^(NUMERIC|DECIMAL|DATETIME)$/.test(declared)) return SHORT_BYTES;
  if (DECODED.test(declared)) return VALUE_OVERHEAD + DECODED_FACTOR * CELL_CHARS;
  if (SHORT_TYPES.test(declared)) return SHORT_BYTES;
  const sized = SIZED_TEXT.exec(declared) ?? SIZED_NUMERIC.exec(declared);
  if (sized !== null) {
    const chars = Math.min(Number(sized[1]) + 2, CELL_CHARS);
    return VALUE_OVERHEAD + 2 * chars;
  }
  return long;
}

/** How many rows a page of these columns may ask for so that it stays within `PAGE_BYTES` (at least 1). */
export function pageLimit(
  engine: Engine,
  types: (string | null)[],
  chosen: number,
  budget = PAGE_BYTES,
): number {
  let row = ROW_OVERHEAD;
  for (const type of types) row += worstValueBytes(engine, type);
  return Math.max(1, Math.min(chosen, Math.floor(budget / row)));
}

/** How many rows fit in the budget at the widest row seen so far (`widest` bytes; 0: none seen yet). */
export function observedLimit(widest: number, chosen: number, budget = PAGE_BYTES): number {
  if (widest <= 0) return chosen;
  return Math.max(1, Math.min(chosen, Math.floor(budget / widest)));
}

/** Whether a value's text holds the search (case is ignored; database text is never shown as HTML). */
function holds(value: unknown, needle: string): boolean {
  if (value === null || value === undefined) return false;
  return fullText(value).toLocaleLowerCase().includes(needle);
}

/** The rows of one page for the grid, with the in-page search: `pageIndex` maps a shown row to the page's row. */
export class PageRows implements GridRows {
  private readonly rows: CachedRow[];
  private readonly shown: number[];
  /** The estimated bytes of the page. */
  readonly bytes: number;

  constructor(values: unknown[][], truncatedCells: [number, number][], search = '') {
    this.rows = values.map((row) => ({ values: row, cut: new Set<number>() }));
    for (const [row, column] of truncatedCells) this.rows[row]?.cut.add(column);
    let bytes = 0;
    for (const row of values) bytes += rowBytes(row);
    this.bytes = bytes;
    const needle = search.trim().toLocaleLowerCase();
    this.shown =
      needle === ''
        ? this.rows.map((_, index) => index)
        : this.rows.flatMap((row, index) =>
            row.values.some((value) => holds(value, needle)) ? [index] : [],
          );
  }

  get rowCount(): number {
    return this.shown.length;
  }

  /** The rows of the page, before the search. */
  get pageRowCount(): number {
    return this.rows.length;
  }

  get(index: number): CachedRow | undefined {
    const row = this.shown[index];
    return row === undefined ? undefined : this.rows[row];
  }

  /** The page's own index of a shown row (the cell detail asks the server by it). */
  pageIndex(index: number): number | undefined {
    return this.shown[index];
  }

  subscribe(): () => void {
    // A page never changes: a new page or search is a new `PageRows`.
    return () => undefined;
  }
}
