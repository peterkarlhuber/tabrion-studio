/**
 * The result grid (03 section 2 C, 08 sections 4.4, 4.5 and 9): column names and types, the rows of one result,
 * virtualized in both directions with TanStack Virtual, so a result of 100 000 rows renders only what is visible and
 * holds only the chunks of `RowCache` (at most 10 000 rows and 64 MB) in the browser. The data viewer (STU-013) shows
 * one page of a table in it through the same `GridRows` interface (`views/viewer/pageRows.ts`).
 *
 * Accessibility (WAI-ARIA grid pattern with `aria-activedescendant`, 08 section 9): `role="grid"` with
 * `aria-rowcount`/`aria-colcount`, every rendered row and cell with `aria-rowindex`/`aria-colindex`. The grid is one
 * tab stop; arrows, Home/End, Ctrl+Home/End and Page Up/Down move the active cell, Enter opens it in the detail
 * panel, ⌘C/Ctrl+C copies its value. NULL is shown as an italic "NULL" in its own color, a value cut by the server
 * or here gets a "⋯" and the words "cut" for screen readers.
 */
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  useCallback,
  useEffect,
  useId,
  useReducer,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
} from 'react';
import { useTranslation } from 'react-i18next';

import type { QueryColumn } from '../../api/queries';
import clsx from '../../lib/clsx';
import { useTheme } from '../../theme';
import { cellKind, fullText, previewText } from './format';
import type { GridRows } from './rowCache';
import styles from './Editor.module.css';

export interface ActiveCell {
  row: number;
  column: number;
}

const ROW_HEIGHT = { compact: 28, normal: 36 } as const;

/** A column's width from its name and type (no resizing yet: docs/TODO.md TD-033). */
export function columnWidth(column: QueryColumn): number {
  const chars = Math.max(column.name.length, column.type.length, 6);
  return Math.min(Math.max(chars * 8 + 40, 96), 320);
}

export function ResultGrid({
  cache,
  columns,
  label,
  onOpen,
  onCopy,
}: {
  cache: GridRows;
  columns: QueryColumn[];
  label: string;
  /** Enter (`focus` true: the panel takes the focus) or a click (the grid keeps it): show it in the detail panel. */
  onOpen: (cell: ActiveCell, value: unknown, cut: boolean, focus: boolean) => void;
  /** After ⌘C/Ctrl+C: whether the value reached the clipboard. */
  onCopy: (copied: boolean) => void;
}) {
  const { t } = useTranslation();
  const { density } = useTheme();
  const rowHeight = ROW_HEIGHT[density];
  const id = useId();
  const scroller = useRef<HTMLDivElement>(null);
  const [, refresh] = useReducer((n: number) => n + 1, 0);
  const [active, setActive] = useState<ActiveCell>({ row: 0, column: 0 });
  const rowCount = cache.rowCount;

  useEffect(() => cache.subscribe(refresh), [cache]);

  // TanStack Virtual returns fresh functions each render; the React Compiler skips this component, which is fine:
  // it re-renders on scroll anyway.
  // eslint-disable-next-line react-hooks/incompatible-library
  const rows = useVirtualizer({
    count: rowCount,
    getScrollElement: () => scroller.current,
    estimateSize: () => rowHeight,
    overscan: 10,
    // The sticky header row is above the first row.
    paddingStart: rowHeight,
    scrollPaddingStart: rowHeight,
    initialRect: { width: 800, height: 400 },
  });
  const cols = useVirtualizer({
    horizontal: true,
    count: columns.length,
    getScrollElement: () => scroller.current,
    estimateSize: (index) => columnWidth(columns[index] ?? { name: '', type: '' }),
    overscan: 3,
    initialRect: { width: 800, height: 400 },
  });

  useEffect(() => {
    rows.measure();
  }, [rowHeight, rows]);

  const cellId = (cell: ActiveCell) => `${id}-r${cell.row}-c${cell.column}`;

  const move = useCallback(
    (next: ActiveCell) => {
      const row = Math.max(0, Math.min(rowCount - 1, next.row));
      const column = Math.max(0, Math.min(columns.length - 1, next.column));
      setActive({ row, column });
      rows.scrollToIndex(row, { align: 'auto' });
      cols.scrollToIndex(column, { align: 'auto' });
    },
    [rowCount, columns.length, rows, cols],
  );

  const pageRows = Math.max(1, Math.floor((scroller.current?.clientHeight ?? 400) / rowHeight) - 2);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (rowCount === 0 || columns.length === 0) return;
    const mod = event.ctrlKey || event.metaKey;
    const { row, column } = active;
    let next: ActiveCell;
    switch (event.key) {
      case 'ArrowDown':
        next = { row: row + 1, column };
        break;
      case 'ArrowUp':
        next = { row: row - 1, column };
        break;
      case 'ArrowRight':
        next = { row, column: column + 1 };
        break;
      case 'ArrowLeft':
        next = { row, column: column - 1 };
        break;
      case 'Home':
        next = mod ? { row: 0, column: 0 } : { row, column: 0 };
        break;
      case 'End':
        next = mod
          ? { row: rowCount - 1, column: columns.length - 1 }
          : { row, column: columns.length - 1 };
        break;
      case 'PageDown':
        next = { row: row + pageRows, column };
        break;
      case 'PageUp':
        next = { row: row - pageRows, column };
        break;
      case 'Enter': {
        const cached = cache.get(row);
        if (cached !== undefined) {
          event.preventDefault();
          onOpen(active, cached.values[column], cached.cut.has(column), true);
        }
        return;
      }
      default:
        if (mod && !event.altKey && event.key.toLowerCase() === 'c') {
          const cached = cache.get(row);
          if (cached === undefined) return;
          event.preventDefault();
          const text = fullText(cached.values[column]);
          void (async () => {
            try {
              await navigator.clipboard.writeText(text);
              onCopy(true);
            } catch {
              onCopy(false);
            }
          })();
        }
        return;
    }
    event.preventDefault();
    move(next);
  };

  /** A click on a cell makes it the active one and opens it (08 section 4.4); the grid keeps the focus. */
  const onClick = (event: MouseEvent<HTMLDivElement>) => {
    const target = (event.target as HTMLElement).closest<HTMLElement>('[data-row]');
    if (target === null) return;
    const cell = { row: Number(target.dataset.row), column: Number(target.dataset.column) };
    setActive(cell);
    const cached = cache.get(cell.row);
    if (cached !== undefined) {
      onOpen(cell, cached.values[cell.column], cached.cut.has(cell.column), false);
    }
  };

  const totalWidth = cols.getTotalSize();
  const activeRendered =
    rows.getVirtualItems().some((item) => item.index === active.row) &&
    cols.getVirtualItems().some((item) => item.index === active.column);

  return (
    <div
      ref={scroller}
      className={styles.grid}
      role="grid"
      aria-label={label}
      aria-rowcount={rowCount + 1}
      aria-colcount={columns.length}
      aria-readonly="true"
      aria-activedescendant={activeRendered && rowCount > 0 ? cellId(active) : undefined}
      tabIndex={0}
      onKeyDown={onKeyDown}
      onClick={onClick}
      data-testid="result-grid"
    >
      <div
        role="rowgroup"
        className={styles.gridSizer}
        style={{ height: rows.getTotalSize(), width: totalWidth }}
      >
        <div
          role="row"
          aria-rowindex={1}
          className={styles.headerRow}
          style={{ height: rowHeight, width: totalWidth }}
        >
          {cols.getVirtualItems().map((item) => {
            const column = columns[item.index] as QueryColumn;
            return (
              <div
                key={item.key}
                role="columnheader"
                aria-colindex={item.index + 1}
                className={styles.headerCell}
                style={{ width: item.size, transform: `translateX(${item.start}px)` }}
                title={column.type ? `${column.name} (${column.type})` : column.name}
              >
                <span className={styles.columnName}>{previewText(column.name)}</span>
                {column.type ? (
                  <span className={styles.columnType}>
                    <span className="tb-sr-only">, </span>
                    {column.type}
                  </span>
                ) : null}
              </div>
            );
          })}
        </div>
        {rows.getVirtualItems().map((rowItem) => {
          const cached = cache.get(rowItem.index);
          return (
            <div
              key={rowItem.key}
              role="row"
              aria-rowindex={rowItem.index + 2}
              aria-busy={cached === undefined ? true : undefined}
              className={styles.row}
              style={{
                height: rowItem.size,
                width: totalWidth,
                transform: `translateY(${rowItem.start}px)`,
              }}
            >
              {cols.getVirtualItems().map((colItem) => {
                const cell = { row: rowItem.index, column: colItem.index };
                const value = cached?.values[colItem.index];
                const cut = cached?.cut.has(colItem.index) ?? false;
                const kind = cached === undefined ? 'loading' : cellKind(value);
                const isActive = active.row === cell.row && active.column === cell.column;
                return (
                  <div
                    key={colItem.key}
                    id={cellId(cell)}
                    role="gridcell"
                    aria-colindex={colItem.index + 1}
                    className={clsx(
                      styles.cell,
                      kind === 'null' && styles.null,
                      (kind === 'number' || kind === 'boolean') && styles.numeric,
                      isActive && styles.activeCell,
                    )}
                    style={{ width: colItem.size, transform: `translateX(${colItem.start}px)` }}
                    data-row={cell.row}
                    data-column={cell.column}
                  >
                    {cached === undefined ? (
                      <span aria-hidden="true">…</span>
                    ) : (
                      <>
                        <span className={styles.cellText}>{previewText(value)}</span>
                        {cut ? (
                          <span className={styles.cutMark}>
                            <span aria-hidden="true">⋯</span>
                            <span className="tb-sr-only">{t('editor.grid.cut')}</span>
                          </span>
                        ) : null}
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}
