/**
 * The rows of one result in the browser: a few pages around what is visible, never the whole result (03 section 6:
 * results stay in the backend; acceptance of STU-012: 100 000 rows within the memory limit of 03 section 10, 300 MB).
 *
 * The grid asks `rowCache.get(index)` for every visible row; a missing page is fetched once (`PAGE_ROWS` rows) and
 * the least recently used page is dropped when more than `MAX_PAGES` are held. So the browser holds at most
 * `MAX_PAGES * PAGE_ROWS` rows (10 000 by default) of a result of any size.
 */
import { fetchRows, PAGE_ROWS, type RowPage } from '../../api/queries';
import type { Problem } from '../../api/problem';

/** How many pages one result keeps (20 x 500 = 10 000 rows). */
export const MAX_PAGES = 20;

export interface CachedRow {
  values: unknown[];
  /** Columns whose value the server cut (64 KB of text, 32 KB of bytes). */
  cut: Set<number>;
}

export class RowCache {
  private readonly pages = new Map<number, CachedRow[]>();
  private readonly loading = new Set<number>();
  private problemSeen: Problem | null = null;
  private readonly listeners = new Set<() => void>();
  private readonly abort = new AbortController();

  constructor(
    private readonly fetcher: typeof fetch,
    readonly queryId: string,
    readonly rowCount: number,
    private readonly maxPages = MAX_PAGES,
    private readonly pageRows = PAGE_ROWS,
  ) {}

  /** The row, or undefined while its page loads (the fetch starts on the first ask). */
  get(index: number): CachedRow | undefined {
    if (index < 0 || index >= this.rowCount) return undefined;
    const page = Math.floor(index / this.pageRows);
    const rows = this.pages.get(page);
    if (rows === undefined) {
      this.load(page);
      return undefined;
    }
    // Most recently used last.
    this.pages.delete(page);
    this.pages.set(page, rows);
    return rows[index - page * this.pageRows];
  }

  /** Why a page could not be loaded (the result may be gone: the server keeps the last 16 queries). */
  get problem(): Problem | null {
    return this.problemSeen;
  }

  /** How many rows the cache holds now; never more than `maxPages * pageRows`. */
  get heldRows(): number {
    let total = 0;
    for (const rows of this.pages.values()) total += rows.length;
    return total;
  }

  get heldPages(): number {
    return this.pages.size;
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  dispose(): void {
    this.abort.abort();
    this.listeners.clear();
    this.pages.clear();
  }

  private load(page: number): void {
    if (this.loading.has(page) || this.problemSeen !== null || this.abort.signal.aborted) return;
    this.loading.add(page);
    void fetchRows(
      this.fetcher,
      this.queryId,
      page * this.pageRows,
      this.pageRows,
      this.abort.signal,
    ).then((result) => {
      this.loading.delete(page);
      if (this.abort.signal.aborted) return;
      if (result.ok) this.store(page, result.value);
      else this.problemSeen = result.problem;
      for (const listener of this.listeners) listener();
    });
  }

  private store(page: number, value: RowPage): void {
    const rows: CachedRow[] = value.rows.map((values) => ({ values, cut: new Set<number>() }));
    for (const [row, column] of value.truncatedCells) rows[row]?.cut.add(column);
    this.pages.set(page, rows);
    while (this.pages.size > this.maxPages) {
      const oldest = this.pages.keys().next().value;
      if (oldest === undefined) break;
      this.pages.delete(oldest);
    }
  }
}
