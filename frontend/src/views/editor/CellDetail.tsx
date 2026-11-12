/**
 * The detail panel of one cell (08 section 4.4): the whole value as plain text (never HTML, threat T-07), JSON
 * indented, bytes as a hex dump, with "Copy". Opened with Enter (the focus moves into the panel) or a click in the
 * grid (the focus stays in the grid); Escape or "Close" returns the focus to the grid.
 */
import { Copy, X } from 'lucide-react';
import { useEffect, useRef, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';

import type { QueryColumn } from '../../api/queries';
import { Button } from '../../components/ui/Button';
import { IconButton } from '../../components/ui/IconButton';
import {
  byteLength,
  cellKind,
  formatCount,
  fullText,
  isBytes,
  markInvisible,
  oneLine,
} from './format';
import styles from './Editor.module.css';

export interface OpenCell {
  row: number;
  column: number;
  value: unknown;
  cut: boolean;
  /** Move the focus into the panel: on Enter, not on a click, which keeps it in the grid (L-7). */
  focus?: boolean;
}

export function CellDetail({
  cell,
  column,
  locale,
  onClose,
  onCopy,
}: {
  cell: OpenCell;
  column: QueryColumn;
  locale: string;
  onClose: () => void;
  onCopy: (copied: boolean) => void;
}) {
  const { t } = useTranslation();
  const heading = useRef<HTMLHeadingElement>(null);
  const kind = cellKind(cell.value);

  useEffect(() => {
    if (cell.focus) heading.current?.focus();
  }, [cell.row, cell.column, cell.focus]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(fullText(cell.value));
      onCopy(true);
    } catch {
      onCopy(false);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      onClose();
    }
  };

  return (
    // The panel is a labelled region; Escape inside it closes it like the close button.
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <aside
      className={styles.detail}
      aria-labelledby="cell-detail-title"
      onKeyDown={onKeyDown}
      data-testid="cell-detail"
    >
      <div className={styles.detailHeader}>
        <h3 id="cell-detail-title" ref={heading} tabIndex={-1} className={styles.detailTitle}>
          {t('editor.detail.title', {
            column: oneLine(column.name),
            row: formatCount(cell.row + 1, locale),
            interpolation: { escapeValue: false },
          })}
        </h3>
        <IconButton label={t('editor.detail.close')} icon={<X />} onClick={onClose} />
      </div>
      <p className={styles.detailMeta}>
        {kind === 'bytes' && isBytes(cell.value)
          ? t('editor.detail.bytes', { count: byteLength(cell.value) })
          : t(`editor.detail.kind.${kind}`)}
        {column.type ? ` · ${column.type}` : ''}
      </p>
      {cell.cut ? (
        <p className={styles.detailCut} data-testid="cell-cut">
          {t('editor.detail.cut')}
        </p>
      ) : null}
      {/* A scrollable region must be reachable by keyboard (WCAG 2.1.1). */}
      <pre
        className={styles.detailValue}
        role="region"
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
        tabIndex={0}
        aria-label={t('editor.detail.value')}
        data-testid="cell-value"
      >
        {kind === 'null' ? (
          <span className={styles.null}>NULL</span>
        ) : (
          markInvisible(fullText(cell.value))
        )}
      </pre>
      <div>
        <Button size="sm" icon={<Copy />} onClick={() => void copy()}>
          {t('editor.detail.copy')}
        </Button>
      </div>
    </aside>
  );
}
