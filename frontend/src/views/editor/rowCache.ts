/**
 * The rows of one result in the browser: a few chunks around what is visible, never the whole result (03 section 6:
 * results stay in the backend; acceptance of STU-012: 100 000 rows within the memory limit of 03 section 10, 300 MB).
 *
 * The grid asks `rowCache.get(index)` for every visible row. Rows are held in chunks of `CHUNK_ROWS`; a missing chunk
 * is fetched together with the missing chunks after it, as many as fit in `FETCH_BYTES` by the size of the rows seen
 * so far (one chunk until a row was seen, at most `PAGE_ROWS` rows). The least recently used chunks are dropped while
 * more than `MAX_ROWS` rows or more than `MAX_BYTES` (estimated) are held, so the bound holds for wide rows too: the
 * server allows a result of up to 200 MB (`max_result_mb`) and a cell of up to 64 KB of text (M-4 of
 * stu-012-review.md). The estimate counts text as UTF-16 (2 bytes per code unit, as a JavaScript string may hold it)
 * plus a fixed overhead per value and row.
 *
 * A page that cannot be loaded, or one that is not the page asked for (offset or row count), is asked for again after
 * a pause, up to `MAX_ATTEMPTS` times in a row; a result the server no longer has (`studio.query_not_found`) is not.
 */
import { fetchRows, PAGE_ROWS, type RowPage } from '../../api/queries';
import type { Problem } from '../../api/problem';

/** Rows per chunk, the unit the cache holds and drops. */
export const CHUNK_ROWS = 50;
/** The most rows one result keeps (200 chunks). */
export const MAX_ROWS = 10_000;
/** The most bytes (estimated) one result keeps. */
export const MAX_BYTES = 64 * 1024 * 1024;
/** About how many bytes one fetch loads, once the size of a row is known. */
export const FETCH_BYTES = 4 * 1024 * 1024;
/** How often a page is asked for before its problem is shown. */
export const MAX_ATTEMPTS = 3;
const RETRY_MS = 1_000;
const VALUE_OVERHEAD = 16;
const ROW_OVERHEAD = 64;

export interface CachedRow {
  values: unknown[];
  /** Columns whose value the server cut (64 KB of text, 32 KB of bytes). */
  cut: Set<number>;
}

/** What the grid reads rows from: the editor's `RowCache`, or one page of the data viewer (STU-013). */
export interface GridRows {
  readonly rowCount: number;
  /** The row, or undefined while it loads. */
  get(index: number): CachedRow | undefined;
  subscribe(listener: () => void): () => void;
}

interface Chunk {
  rows: CachedRow[];
  bytes: number;
}

export interface RowCacheOptions {
  chunkRows?: number;
  maxRows?: number;
  maxBytes?: number;
  fetchBytes?: number;
  retryMs?: number;
}

/** The estimated size of a value in a JavaScript heap. */
export function valueBytes(value: unknown): number {
  if (typeof value === 'string') return VALUE_OVERHEAD + 2 * value.length;
  if (value === null || typeof value !== 'object') return VALUE_OVERHEAD;
  try {
    return VALUE_OVERHEAD * 2 + 2 * JSON.stringify(value).length;
  } catch {
    return VALUE_OVERHEAD * 2;
  }
}

export function rowBytes(values: unknown[]): number {
  let total = ROW_OVERHEAD;
  for (const value of values) total += valueBytes(value);
  return total;
}

export class RowCache implements GridRows {
  private readonly chunks = new Map<number, Chunk>();
  private readonly loading = new Set<number>();
  private bytes = 0;
  private seenRows = 0;
  private seenBytes = 0;
  private failures = 0;
  private retryAt = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private problemSeen: Problem | null = null;
  private readonly listeners = new Set<() => void>();
  private readonly abort = new AbortController();
  private readonly chunkRows: number;
  private readonly maxChunks: number;
  private readonly maxBytes: number;
  private readonly fetchBytes: number;
  private readonly retryMs: number;

  constructor(
    private readonly fetcher: typeof fetch,
    readonly queryId: string,
    readonly rowCount: number,
    options: RowCacheOptions = {},
  ) {
    this.chunkRows = options.chunkRows ?? CHUNK_ROWS;
    this.maxChunks = Math.max(1, Math.floor((options.maxRows ?? MAX_ROWS) / this.chunkRows));
    this.maxBytes = options.maxBytes ?? MAX_BYTES;
    this.fetchBytes = options.fetchBytes ?? FETCH_BYTES;
    this.retryMs = options.retryMs ?? RETRY_MS;
  }

  /** The row, or undefined while its chunk loads (the fetch starts on the first ask). */
  get(index: number): CachedRow | undefined {
    if (index < 0 || index >= this.rowCount) return undefined;
    const number = Math.floor(index / this.chunkRows);
    const chunk = this.chunks.get(number);
    if (chunk === undefined) {
      this.load(number);
      return undefined;
    }
    // Most recently used last.
    this.chunks.delete(number);
    this.chunks.set(number, chunk);
    return chunk.rows[index - number * this.chunkRows];
  }

  /** Why rows could not be loaded (the result may be gone: the server keeps the last 16 queries). */
  get problem(): Problem | null {
    return this.problemSeen;
  }

  /** How many rows the cache holds now; never more than `MAX_ROWS`. */
  get heldRows(): number {
    let total = 0;
    for (const chunk of this.chunks.values()) total += chunk.rows.length;
    return total;
  }

  get heldChunks(): number {
    return this.chunks.size;
  }

  /** The estimated bytes held; at most `MAX_BYTES` plus the last fetch. */
  get heldBytes(): number {
    return this.bytes;
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  dispose(): void {
    this.abort.abort();
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.listeners.clear();
    this.chunks.clear();
    this.bytes = 0;
  }

  private notify(): void {
    for (const listener of this.listeners) listener();
  }

  /** How many chunks one fetch asks for: one until a row was seen, then as many as fit in `fetchBytes`. */
  private fetchChunks(): number {
    const most = Math.max(1, Math.floor(PAGE_ROWS / this.chunkRows));
    if (this.seenRows === 0) return 1;
    const perChunk = (this.seenBytes / this.seenRows) * this.chunkRows;
    return Math.min(most, Math.max(1, Math.floor(this.fetchBytes / perChunk)));
  }

  private load(first: number): void {
    if (this.loading.has(first) || this.problemSeen !== null || this.abort.signal.aborted) return;
    if (Date.now() < this.retryAt) return;
    const last = Math.ceil(this.rowCount / this.chunkRows) - 1;
    let count = 1;
    const want = this.fetchChunks();
    while (
      count < want &&
      first + count <= last &&
      !this.chunks.has(first + count) &&
      !this.loading.has(first + count)
    ) {
      count += 1;
    }
    for (let i = 0; i < count; i += 1) this.loading.add(first + i);
    const offset = first * this.chunkRows;
    const limit = Math.min(count * this.chunkRows, this.rowCount - offset);
    void fetchRows(this.fetcher, this.queryId, offset, limit, this.abort.signal).then((result) => {
      for (let i = 0; i < count; i += 1) this.loading.delete(first + i);
      if (this.abort.signal.aborted) return;
      if (result.ok && result.value.offset === offset && result.value.rows.length === limit) {
        this.failures = 0;
        this.store(first, count, result.value);
      } else {
        this.fail(
          result.ok
            ? { code: 'studio.rows_invalid', kind: 'internal', status: 0, logId: null }
            : result.problem,
        );
      }
      this.notify();
    });
  }

  private fail(problem: Problem): void {
    this.failures += 1;
    if (problem.code === 'studio.query_not_found' || this.failures >= MAX_ATTEMPTS) {
      this.problemSeen = problem;
      return;
    }
    // Ask again after a pause: the grid asks for its visible rows when the listeners are told.
    const delay = this.retryMs * this.failures;
    this.retryAt = Date.now() + delay;
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      this.retryAt = 0;
      if (!this.abort.signal.aborted) this.notify();
    }, delay);
  }

  private store(first: number, count: number, page: RowPage): void {
    const rows: CachedRow[] = page.rows.map((values) => ({ values, cut: new Set<number>() }));
    for (const [row, column] of page.truncatedCells) rows[row]?.cut.add(column);
    for (let i = 0; i < count; i += 1) {
      const part = rows.slice(i * this.chunkRows, (i + 1) * this.chunkRows);
      let bytes = 0;
      for (const row of part) bytes += rowBytes(row.values);
      this.seenRows += part.length;
      this.seenBytes += bytes;
      const old = this.chunks.get(first + i);
      if (old !== undefined) {
        this.bytes -= old.bytes;
        this.chunks.delete(first + i);
      }
      this.chunks.set(first + i, { rows: part, bytes });
      this.bytes += bytes;
    }
    // Drop the least recently used chunks, never the ones just stored.
    while (
      this.chunks.size > count &&
      (this.chunks.size > this.maxChunks || this.bytes > this.maxBytes)
    ) {
      const oldest = this.chunks.keys().next().value;
      if (oldest === undefined) break;
      this.bytes -= this.chunks.get(oldest)?.bytes ?? 0;
      this.chunks.delete(oldest);
    }
  }
}
