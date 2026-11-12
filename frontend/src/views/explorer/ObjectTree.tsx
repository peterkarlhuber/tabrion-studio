/**
 * The object tree (STU-010, 08 sections 4.3 and 9): a WAI-ARIA tree, single selection, with a roving tab stop.
 *
 * - ↓ / ↑: next / previous row; Home / End: first / last row.
 * - →: open a closed row; on an open one, go to its first child. ←: close an open row; else go to the parent. While
 *   a filter is active the groups stay open, so ← goes to the parent (L-3 of stu-010-ui-review.md).
 * - Enter or Space: select an object (its details load); on a schema or group row, open or close it.
 * - A letter or digit: the next row whose name starts with it.
 *
 * Selection does not follow the focus: moving through 500 tables must not send 500 detail requests. The rows are
 * rendered flat (`aria-level`, `aria-posinset`, `aria-setsize`), so that a virtualized list can take over (TD-041).
 */
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react';

import clsx from '../../lib/clsx';
import styles from './Explorer.module.css';
import type { TreeRow } from './tree';

export interface FocusTarget {
  id: string;
  /** Changes for every request, so that the same row can be asked for twice. */
  seq: number;
}

export function ObjectTree({
  rows,
  label,
  selected,
  focused,
  focusTarget,
  icon,
  content,
  text,
  onFocusRow,
  onToggle,
  onSelect,
}: {
  rows: TreeRow[];
  /** The tree's accessible name. */
  label: string;
  selected: string | null;
  focused: string | null;
  /** A row to move the focus to once it is rendered (a relationship followed from the details). */
  focusTarget: FocusTarget | null;
  icon: (row: TreeRow) => ReactNode;
  content: (row: TreeRow) => ReactNode;
  /** The row's name for type-ahead. */
  text: (row: TreeRow) => string;
  onFocusRow: (id: string) => void;
  onToggle: (id: string, open: boolean) => void;
  onSelect: (row: TreeRow) => void;
}) {
  const elements = useRef(new Map<string, HTMLLIElement>());
  const visible = rows.some((row) => row.id === focused);
  const stop = visible
    ? focused
    : rows.some((row) => row.id === selected)
      ? selected
      : (rows[0]?.id ?? null);

  useEffect(() => {
    if (focusTarget === null) return;
    elements.current.get(focusTarget.id)?.focus();
  }, [focusTarget]);

  const move = (id: string | undefined) => {
    if (id === undefined) return;
    onFocusRow(id);
    elements.current.get(id)?.focus();
  };

  const activate = (row: TreeRow) => {
    if (row.type === 'schema' || row.type === 'group') {
      if (row.expanded !== undefined && !row.forced) onToggle(row.id, !row.expanded);
    } else {
      onSelect(row);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLLIElement>, index: number) => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.target !== event.currentTarget)
      return;
    const row = rows[index];
    if (row === undefined) return;
    let handled = true;
    switch (event.key) {
      case 'ArrowDown':
        move(rows[index + 1]?.id);
        break;
      case 'ArrowUp':
        move(rows[index - 1]?.id);
        break;
      case 'Home':
        move(rows[0]?.id);
        break;
      case 'End':
        move(rows[rows.length - 1]?.id);
        break;
      case 'ArrowRight':
        if (row.expanded === false) onToggle(row.id, true);
        else if (row.expanded === true && rows[index + 1]?.parent === row.id) {
          move(rows[index + 1]?.id);
        }
        break;
      case 'ArrowLeft':
        if (row.expanded === true && !row.forced) onToggle(row.id, false);
        else if (row.parent !== null) move(row.parent);
        break;
      case 'Enter':
      case ' ':
        activate(row);
        break;
      default:
        handled = false;
        if (event.key.length === 1 && /\S/.test(event.key)) {
          const char = event.key.toLocaleLowerCase();
          const order = [...rows.slice(index + 1), ...rows.slice(0, index + 1)];
          const next = order.find((item) => text(item).toLocaleLowerCase().startsWith(char));
          if (next !== undefined) {
            move(next.id);
            handled = true;
          }
        }
    }
    if (handled) event.preventDefault();
  };

  return (
    <ul role="tree" aria-label={label} className={styles.tree}>
      {rows.map((row, index) => {
        const selectable =
          row.type === 'object' || row.type === 'extensions' || row.type === 'file';
        return (
          <li
            key={row.id}
            ref={(element) => {
              if (element === null) elements.current.delete(row.id);
              else elements.current.set(row.id, element);
            }}
            role="treeitem"
            aria-level={row.level}
            aria-posinset={row.posinset}
            aria-setsize={row.setsize}
            aria-expanded={row.expanded}
            aria-selected={selectable ? row.id === selected : undefined}
            tabIndex={row.id === stop ? 0 : -1}
            className={clsx(styles.row, row.id === selected && styles.rowSelected)}
            style={{ paddingInlineStart: `calc(${row.level - 1} * 1.25rem + 0.25rem)` }}
            data-testid="tree-row"
            data-row-type={row.type}
            onFocus={(event) => {
              if (event.target === event.currentTarget) onFocusRow(row.id);
            }}
            onClick={() => {
              onFocusRow(row.id);
              activate(row);
            }}
            onKeyDown={(event) => onKeyDown(event, index)}
          >
            <span className={styles.chevron} aria-hidden="true">
              {row.expanded === true ? (
                <ChevronDown />
              ) : row.expanded === false ? (
                <ChevronRight />
              ) : null}
            </span>
            <span className={styles.rowIcon} aria-hidden="true">
              {icon(row)}
            </span>
            <span className={styles.rowLabel}>{content(row)}</span>
          </li>
        );
      })}
    </ul>
  );
}
