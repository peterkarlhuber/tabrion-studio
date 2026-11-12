/**
 * The data viewer (STU-013 page, 03 section 2 D, 08 section 4.5): the "Data" tab of a table, view or materialized
 * view in the explorer. It shows one page at a time in the editor's grid (`ResultGrid`, WAI-ARIA grid), sorted by
 * one column, filtered by up to 20 conditions the server binds as parameters, with "Previous" / "Next" through the
 * server's keyset pages, a search in the current page and the cell detail with "Load in full".
 *
 * - **Read-only:** the only requests are the viewer's two GET routes (`api/viewer.ts`); there is no control that
 *   changes data (STU-026 stays locked).
 * - **D-02 and the connection:** the tab reads on the explorer's open connection, which the explorer opened through
 *   the shared PROD confirmation (STU-008); the tab never opens one. A connection that is gone is handed back to the
 *   explorer (`onGone`), which drops it.
 * - **Bound to the connection and object:** the explorer renders the tab with a key of connection, schema and name,
 *   so a profile switch, a new connection or another object starts with nothing (the C-1 pattern of the editor).
 * - **Stale answers:** every load counts up `request` and aborts the one before; an answer is shown only for the
 *   latest, and "Load in full" only for the cell it was asked for (and is aborted when the cell is left).
 * - **Memory:** a page asks for no more rows than fit in 64 MB at the server's largest values (`pageRows.ts`).
 * - **No read in passing:** the first page is asked for `FIRST_READ_DELAY` after the tab opens, so arrowing across
 *   the Data tab, or through objects while it is the open tab, starts no query (L-3 of stu-013-page-review.md).
 * - **Request size:** filters that would make the request longer than the server takes are refused in the form
 *   (`queryFits`, M-1 of stu-013-page-review.md).
 */
import { ChevronLeft, ChevronRight, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useId, useMemo, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';

import type { Engine } from '../../api/explorer';
import type { Problem } from '../../api/problem';
import type { QueryColumn } from '../../api/queries';
import {
  fetchCell,
  fetchPage,
  FILTER_OPS,
  MAX_FILTERS,
  MAX_LIMIT,
  NO_VALUE_OPS,
  queryFits,
  type FilterOp,
  type ViewerCell,
  type ViewerFilter,
  type ViewerPage,
} from '../../api/viewer';
import { Button } from '../../components/ui/Button';
import { IconButton } from '../../components/ui/IconButton';
import { visible } from '../../lib/visible';
import connectionStyles from '../connections/Connections.module.css';
import { problemText } from '../connections/messages';
import { CellDetail, type OpenCell } from '../editor/CellDetail';
import { formatCount } from '../editor/format';
import { ResultGrid } from '../editor/ResultGrid';
import { rowBytes } from '../editor/rowCache';
import { DEFAULT_PAGE_SIZE, observedLimit, PAGE_SIZES, PageRows, pageLimit } from './pageRows';
import styles from './Viewer.module.css';

/** A connection that is gone: the explorer drops it (and the header too, if it shows it). */
export const GONE = new Set(['studio.connection_not_found', 'studio.connection_stale']);

const VIEWER_PROBLEMS = new Set([
  'viewer_column_unknown',
  'viewer_filter_invalid',
  'viewer_filter_too_long',
  'viewer_sort_unsupported',
  'viewer_page_unknown',
  'viewer_cell_unavailable',
  'viewer_row_gone',
  'query_limit',
]);

export function viewerProblemText(t: TFunction, problem: Problem): string {
  const name = problem.code.replace(/^studio\./, '');
  return problem.code.startsWith('studio.') && VIEWER_PROBLEMS.has(name)
    ? t(`viewer.problems.${name}`)
    : problemText(t, problem);
}

interface Settings {
  sort: string | null;
  desc: boolean;
  filters: ViewerFilter[];
  pageSize: number;
}

/** A filter as typed in the form. */
interface DraftFilter {
  key: number;
  column: string;
  op: FilterOp;
  value: string;
}

type Load =
  | { status: 'loading' }
  | { status: 'problem'; problem: Problem }
  | { status: 'page'; page: ViewerPage };

type Full =
  | { key: string; status: 'loading' }
  | { key: string; status: 'done'; cell: ViewerCell }
  | { key: string; status: 'problem'; problem: Problem };

const INITIAL: Settings = { sort: null, desc: false, filters: [], pageSize: DEFAULT_PAGE_SIZE };

/** Milliseconds between opening the tab and asking for its first page (a tab only passed through asks nothing). */
export const FIRST_READ_DELAY = 150;

export function DataTab({
  fetcher,
  connectionId,
  engine,
  schema,
  name,
  columns,
  onGone,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  engine: Engine;
  schema: string;
  name: string;
  /** The object's columns from the explorer (their types bound the page size before any row is read). */
  columns: { name: string; type: string | null }[];
  onGone: (problem: Problem) => void;
}) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const base = useId();
  const [settings, setSettings] = useState<Settings>(INITIAL);
  /** The `after` token of each page shown so far (null for the first) and the number of its first row. */
  const [trail, setTrail] = useState<{ after: string | null; start: number }[]>([
    { after: null, start: 0 },
  ]);
  const [load, setLoad] = useState<Load>({ status: 'loading' });
  const [search, setSearch] = useState('');
  const [drafts, setDrafts] = useState<DraftFilter[]>([]);
  const [formProblem, setFormProblem] = useState('');
  const [detail, setDetail] = useState<OpenCell | null>(null);
  const [full, setFull] = useState<Full | null>(null);
  const [notice, setNotice] = useState('');
  const request = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const cellRequest = useRef(0);
  const cellAbort = useRef<AbortController | null>(null);
  const nextKey = useRef(1);
  const gridArea = useRef<HTMLDivElement>(null);

  /** The widest row seen on this object so far (estimated bytes): later walks stay within the budget at it. */
  const widest = useRef(0);
  /** The rows per page of the current walk: a `next` token keeps the limit of its first page (the server's rule). */
  const walkLimit = useRef(DEFAULT_PAGE_SIZE);
  const [limit, setLimit] = useState(() =>
    pageLimit(
      engine,
      columns.map((column) => column.type),
      DEFAULT_PAGE_SIZE,
    ),
  );

  /** Drops "Load in full" that is still on its way. */
  const dropCell = () => {
    cellRequest.current += 1;
    cellAbort.current?.abort();
    cellAbort.current = null;
  };

  const read = useCallback(
    async (next: Settings, after: string | null) => {
      const mine = ++request.current;
      cellRequest.current += 1;
      cellAbort.current?.abort();
      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;
      setLoad({ status: 'loading' });
      setDetail(null);
      setFull(null);
      if (after === null) {
        walkLimit.current = observedLimit(
          widest.current,
          pageLimit(
            engine,
            columns.map((column) => column.type),
            next.pageSize,
          ),
        );
        setLimit(walkLimit.current);
      }
      const rows = walkLimit.current;
      const result = await fetchPage(
        fetcher,
        connectionId,
        schema,
        name,
        { limit: rows, sort: next.sort, desc: next.desc, filters: next.filters, after },
        controller.signal,
      );
      if (mine !== request.current) return;
      if (!result.ok) {
        setLoad({ status: 'problem', problem: result.problem });
        if (GONE.has(result.problem.code)) onGone(result.problem);
        return;
      }
      for (const row of result.value.rows) widest.current = Math.max(widest.current, rowBytes(row));
      setLoad({ status: 'page', page: result.value });
    },
    [columns, connectionId, engine, fetcher, name, onGone, schema],
  );

  // The first page when the tab has been open for `FIRST_READ_DELAY` (once: the tab is keyed by connection and
  // object); leaving the tab before asks nothing, and leaving it after drops what is still on its way.
  const latestRead = useRef(read);
  useEffect(() => {
    latestRead.current = read;
  }, [read]);
  useEffect(() => {
    const timer = window.setTimeout(() => void latestRead.current(INITIAL, null), FIRST_READ_DELAY);
    return () => {
      window.clearTimeout(timer);
      request.current += 1;
      cellRequest.current += 1;
      abort.current?.abort();
      cellAbort.current?.abort();
    };
  }, []);

  /** New settings start again at the first page. */
  const apply = (next: Settings) => {
    setSettings(next);
    setTrail([{ after: null, start: 0 }]);
    setSearch('');
    void read(next, null);
  };

  const page = load.status === 'page' ? load.page : null;
  const at = trail.length - 1;
  const current = trail[at] ?? { after: null, start: 0 };
  const rows = useMemo(
    () => (page === null ? null : new PageRows(page.rows, page.truncatedCells, search)),
    [page, search],
  );

  const goNext = () => {
    if (page?.next == null) return;
    const step = { after: page.next, start: current.start + page.rows.length };
    setTrail((list) => [...list, step]);
    setSearch('');
    void read(settings, step.after);
  };

  const goPrevious = () => {
    if (at === 0) return;
    const before = trail[at - 1] ?? { after: null, start: 0 };
    setTrail((list) => list.slice(0, -1));
    setSearch('');
    void read(settings, before.after);
  };

  const refresh = () => {
    setSearch('');
    void read(settings, current.after);
  };

  const firstPage = () => apply(settings);

  const onFilterSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (drafts.some((draft) => draft.column === '')) {
      setFormProblem(t('viewer.filter.chooseColumn'));
      return;
    }
    const filters = drafts.map((draft) => ({
      column: draft.column,
      op: draft.op,
      value: NO_VALUE_OPS.includes(draft.op) ? null : draft.value,
    }));
    // The longest request these filters can make: the largest page and a `next` token later on (M-1).
    if (!queryFits(schema, name, { ...settings, limit: MAX_LIMIT, filters, after: null })) {
      setFormProblem(t('viewer.filter.tooLong'));
      return;
    }
    setFormProblem('');
    apply({ ...settings, filters });
  };

  const addFilter = () => {
    if (drafts.length >= MAX_FILTERS) return;
    const key = nextKey.current++;
    setDrafts((list) => [...list, { key, column: columns[0]?.name ?? '', op: 'eq', value: '' }]);
    // The new filter's column takes the focus.
    window.setTimeout(() => document.getElementById(`${base}-f${key}-column`)?.focus(), 0);
  };

  const removeFilter = (key: number) => {
    setDrafts((list) => list.filter((draft) => draft.key !== key));
    window.setTimeout(() => document.getElementById(`${base}-add`)?.focus(), 0);
  };

  const changeFilter = (key: number, change: Partial<DraftFilter>) =>
    setDrafts((list) => list.map((draft) => (draft.key === key ? { ...draft, ...change } : draft)));

  const clearFilters = () => {
    setDrafts([]);
    setFormProblem('');
    if (settings.filters.length > 0) apply({ ...settings, filters: [] });
  };

  const loadFull = async (cell: OpenCell) => {
    if (page === null || rows === null) return;
    const pageRow = rows.pageIndex(cell.row);
    const column = page.columns[cell.column];
    if (pageRow === undefined || column === undefined) return;
    const key = `${page.page}:${pageRow}:${cell.column}`;
    dropCell();
    const mine = cellRequest.current;
    const controller = new AbortController();
    cellAbort.current = controller;
    setFull({ key, status: 'loading' });
    const result = await fetchCell(
      fetcher,
      connectionId,
      schema,
      name,
      page.page,
      pageRow,
      column.name,
      controller.signal,
    );
    if (mine !== cellRequest.current) return;
    cellAbort.current = null;
    if (!result.ok) {
      setFull({ key, status: 'problem', problem: result.problem });
      if (GONE.has(result.problem.code)) onGone(result.problem);
      return;
    }
    setFull({ key, status: 'done', cell: result.value });
  };

  const detailKey =
    detail !== null && page !== null && rows !== null
      ? `${page.page}:${rows.pageIndex(detail.row) ?? -1}:${detail.column}`
      : null;
  const fullShown = full !== null && full.key === detailKey ? full : null;
  const shownDetail: OpenCell | null =
    detail !== null && fullShown?.status === 'done'
      ? { ...detail, value: fullShown.cell.value, cut: fullShown.cell.truncated }
      : detail;
  const detailColumn: QueryColumn | null =
    detail !== null && page !== null ? (page.columns[detail.column] ?? null) : null;

  const loading = load.status === 'loading';
  const shownCount = rows?.rowCount ?? 0;
  const pageCount = page?.rows.length ?? 0;
  const first = formatCount(current.start + 1, locale);
  const last = formatCount(current.start + pageCount, locale);

  let status = '';
  if (loading) status = t('viewer.loading');
  else if (page !== null) {
    status =
      pageCount === 0
        ? t(settings.filters.length > 0 ? 'viewer.status.noMatch' : 'viewer.status.empty')
        : t(page.next === null ? 'viewer.status.rowsLast' : 'viewer.status.rows', {
            first,
            last,
          });
  }

  const columnOptions = (page?.columns ?? columns).map((column) => column.name);

  return (
    <div className={styles.viewer} data-testid="viewer">
      <div className={styles.controls}>
        <div className={styles.field}>
          <label htmlFor={`${base}-sort`} className={connectionStyles.label}>
            {t('viewer.sort.label')}
          </label>
          <select
            id={`${base}-sort`}
            className={connectionStyles.input}
            value={settings.sort ?? ''}
            data-testid="viewer-sort"
            onChange={(event) => {
              const sort = event.target.value || null;
              // "Table order" has no direction: it goes back to ascending (I-3).
              apply({ ...settings, sort, desc: sort === null ? false : settings.desc });
            }}
          >
            <option value="">{t('viewer.sort.none')}</option>
            {columnOptions.map((column) => (
              <option key={column} value={column}>
                {visible(column)}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.field}>
          <label htmlFor={`${base}-direction`} className={connectionStyles.label}>
            {t('viewer.sort.direction')}
          </label>
          <select
            id={`${base}-direction`}
            className={connectionStyles.input}
            value={settings.desc ? 'desc' : 'asc'}
            disabled={settings.sort === null}
            data-testid="viewer-direction"
            onChange={(event) => apply({ ...settings, desc: event.target.value === 'desc' })}
          >
            <option value="asc">{t('viewer.sort.asc')}</option>
            <option value="desc">{t('viewer.sort.desc')}</option>
          </select>
        </div>
        <div className={styles.field}>
          <label htmlFor={`${base}-size`} className={connectionStyles.label}>
            {t('viewer.pageSize')}
          </label>
          <select
            id={`${base}-size`}
            className={connectionStyles.input}
            value={settings.pageSize}
            data-testid="viewer-page-size"
            onChange={(event) => apply({ ...settings, pageSize: Number(event.target.value) })}
          >
            {PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                {formatCount(size, locale)}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.field}>
          <label htmlFor={`${base}-search`} className={connectionStyles.label}>
            {t('viewer.search.label')}
          </label>
          <input
            id={`${base}-search`}
            type="search"
            className={connectionStyles.input}
            value={search}
            autoComplete="off"
            spellCheck={false}
            disabled={page === null}
            data-testid="viewer-search"
            onChange={(event) => {
              setSearch(event.target.value);
              dropCell();
              setDetail(null);
            }}
          />
        </div>
      </div>

      <form
        className={styles.filters}
        aria-labelledby={`${base}-filters`}
        onSubmit={onFilterSubmit}
        data-testid="viewer-filters"
      >
        <h3 id={`${base}-filters`} className={styles.filtersTitle}>
          {t('viewer.filter.title')}
          {settings.filters.length > 0 ? (
            <span className={styles.applied} data-testid="viewer-filters-applied">
              {' '}
              {t('viewer.filter.applied', { count: settings.filters.length })}
            </span>
          ) : null}
        </h3>
        {drafts.length > 0 ? (
          <ul className={styles.filterRows}>
            {drafts.map((draft, number) => {
              const id = `${base}-f${draft.key}`;
              const position = { number: number + 1 };
              return (
                <li key={draft.key} className={styles.filterRow} data-testid="viewer-filter">
                  <label htmlFor={`${id}-column`} className="tb-sr-only">
                    {t('viewer.filter.column', position)}
                  </label>
                  <select
                    id={`${id}-column`}
                    className={connectionStyles.input}
                    value={draft.column}
                    onChange={(event) => changeFilter(draft.key, { column: event.target.value })}
                  >
                    {columnOptions.map((column) => (
                      <option key={column} value={column}>
                        {visible(column)}
                      </option>
                    ))}
                  </select>
                  <label htmlFor={`${id}-op`} className="tb-sr-only">
                    {t('viewer.filter.op', position)}
                  </label>
                  <select
                    id={`${id}-op`}
                    className={connectionStyles.input}
                    value={draft.op}
                    onChange={(event) =>
                      changeFilter(draft.key, { op: event.target.value as FilterOp })
                    }
                  >
                    {FILTER_OPS.map((op) => (
                      <option key={op} value={op}>
                        {t(`viewer.ops.${op}`)}
                      </option>
                    ))}
                  </select>
                  {NO_VALUE_OPS.includes(draft.op) ? null : (
                    <>
                      <label htmlFor={`${id}-value`} className="tb-sr-only">
                        {t('viewer.filter.value', position)}
                      </label>
                      <input
                        id={`${id}-value`}
                        className={connectionStyles.input}
                        value={draft.value}
                        autoComplete="off"
                        spellCheck={false}
                        maxLength={4096}
                        onChange={(event) => changeFilter(draft.key, { value: event.target.value })}
                      />
                    </>
                  )}
                  <IconButton
                    label={t('viewer.filter.remove', position)}
                    icon={<Trash2 />}
                    onClick={() => removeFilter(draft.key)}
                  />
                </li>
              );
            })}
          </ul>
        ) : null}
        <div className={styles.filterActions}>
          <Button
            id={`${base}-add`}
            size="sm"
            icon={<Plus />}
            aria-disabled={drafts.length >= MAX_FILTERS || undefined}
            aria-describedby={drafts.length >= MAX_FILTERS ? `${base}-most` : undefined}
            onClick={addFilter}
            data-testid="viewer-add-filter"
          >
            {t('viewer.filter.add')}
          </Button>
          <Button size="sm" type="submit" variant="primary" data-testid="viewer-apply">
            {t('viewer.filter.apply')}
          </Button>
          {drafts.length > 0 || settings.filters.length > 0 ? (
            <Button size="sm" onClick={clearFilters} data-testid="viewer-clear">
              {t('viewer.filter.clear')}
            </Button>
          ) : null}
        </div>
        {drafts.length >= MAX_FILTERS ? (
          <p id={`${base}-most`} className={connectionStyles.hint} data-testid="viewer-filter-most">
            {t('viewer.filter.most', { count: MAX_FILTERS })}
          </p>
        ) : null}
        {formProblem !== '' ? (
          <p role="alert" className={connectionStyles.problem}>
            {formProblem}
          </p>
        ) : null}
        <p className={connectionStyles.hint}>
          {t(engine === 'sqlite' ? 'viewer.filter.caseSqlite' : 'viewer.filter.casePostgres')}
        </p>
      </form>

      <div className={styles.statusRow}>
        <p role="status" className={styles.status} data-testid="viewer-status">
          {status}
          {page !== null && search.trim() !== ''
            ? ` ${t('viewer.search.matches', { count: shownCount, total: pageCount })}`
            : ''}
        </p>
        <div className={styles.nav}>
          <Button
            size="sm"
            icon={<ChevronLeft />}
            aria-disabled={at === 0 || loading || undefined}
            onClick={() => {
              if (!loading) goPrevious();
            }}
            data-testid="viewer-previous"
          >
            {t('viewer.previous')}
          </Button>
          <Button
            size="sm"
            icon={<ChevronRight />}
            aria-disabled={page?.next == null || loading || undefined}
            onClick={() => {
              if (!loading) goNext();
            }}
            data-testid="viewer-next"
          >
            {t('viewer.next')}
          </Button>
          <Button
            size="sm"
            icon={<RefreshCw />}
            aria-disabled={loading || undefined}
            onClick={() => {
              if (!loading) refresh();
            }}
            data-testid="viewer-refresh"
          >
            {t('viewer.refresh')}
          </Button>
        </div>
      </div>

      {limit < settings.pageSize ? (
        <p className={connectionStyles.hint} data-testid="viewer-limited">
          {t('viewer.limited', { rows: formatCount(limit, locale) })}
        </p>
      ) : null}
      {page?.hint != null ? (
        <p className={connectionStyles.hint} data-testid="viewer-hint">
          {t(`viewer.hint.${page.hint}`)}
        </p>
      ) : null}
      {page?.cutByBytes ? (
        <p className={connectionStyles.hint} data-testid="viewer-cut">
          {t('viewer.cutByBytes')}
        </p>
      ) : null}
      {notice !== '' ? (
        <p role="status" className={connectionStyles.hint} data-testid="viewer-notice">
          {notice}
        </p>
      ) : null}

      {load.status === 'problem' ? (
        <div className={styles.problem}>
          <p role="alert" className={connectionStyles.problem} data-testid="viewer-problem">
            {viewerProblemText(t, load.problem)}
          </p>
          {load.problem.code === 'studio.viewer_page_unknown' ? (
            <Button size="sm" onClick={firstPage} data-testid="viewer-first-page">
              {t('viewer.firstPage')}
            </Button>
          ) : null}
        </div>
      ) : null}

      {page !== null && rows !== null && page.columns.length > 0 ? (
        <div className={styles.gridArea} ref={gridArea}>
          <ResultGrid
            key={`${page.page}|${search}`}
            cache={rows}
            columns={page.columns}
            label={t('viewer.gridLabel', {
              name: visible(`${schema}.${name}`),
              interpolation: { escapeValue: false },
            })}
            onOpen={(cell, value, cut, focus) => {
              dropCell();
              setFull(null);
              setDetail({ ...cell, value, cut, focus });
            }}
            onCopy={(copied) => setNotice(t(copied ? 'editor.copied' : 'editor.copyFailed'))}
          />
          {shownDetail !== null && detailColumn !== null ? (
            <CellDetail
              cell={shownDetail}
              column={detailColumn}
              locale={locale}
              onClose={() => {
                dropCell();
                setDetail(null);
                setFull(null);
                gridArea.current?.querySelector<HTMLElement>('[role="grid"]')?.focus();
              }}
              onCopy={(copied) => setNotice(t(copied ? 'editor.copied' : 'editor.copyFailed'))}
              cutAction={
                fullShown?.status === 'done' ? undefined : page.paging === 'offset' ? (
                  <p className={connectionStyles.hint}>{t('viewer.full.unavailable')}</p>
                ) : (
                  <div className={styles.full}>
                    <Button
                      size="sm"
                      aria-disabled={fullShown?.status === 'loading' || undefined}
                      onClick={() => {
                        if (detail !== null && fullShown?.status !== 'loading')
                          void loadFull(detail);
                      }}
                      data-testid="viewer-load-full"
                    >
                      {t('viewer.full.load')}
                    </Button>
                    <p role="status" className={connectionStyles.hint} data-testid="viewer-full">
                      {fullShown?.status === 'loading' ? t('viewer.full.loading') : ''}
                      {fullShown?.status === 'problem'
                        ? viewerProblemText(t, fullShown.problem)
                        : ''}
                    </p>
                  </div>
                )
              }
            />
          ) : null}
          {fullShown?.status === 'done' ? (
            <p role="status" className={connectionStyles.hint} data-testid="viewer-full-done">
              {t(fullShown.cell.truncated ? 'viewer.full.cut' : 'viewer.full.done', {
                size: formatCount(fullShown.cell.size, locale),
              })}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
