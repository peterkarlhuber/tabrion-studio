/**
 * The Data Editor's change overview (STU-XDE-1, 08 section 4.6 `ChangeSetReview`): before the preview and in the
 * review, every change of the changeset by row. A changed row lists its changed columns with the value before and
 * after; a new row the values it sets (the other columns get their defaults); a deleted row its key. NULL and the
 * empty text are told apart; bytes are shown by their size (G-S2 6A). The values are the page's own (the loaded rows and the edits); nothing here is sent.
 */
import { useTranslation } from 'react-i18next';

import { isBytes } from '../../api/cellValues';
import { type Change, type Row } from '../../api/dataEditor';
import { BytesSize } from './CellEditor';
import styles from './DataEditor.module.css';

/** Longer values are cut in the overview (the cell keeps the whole value). */
const SHOWN_CHARS = 120;

function ShownValue({ value }: { value: unknown }) {
  const { t } = useTranslation();
  if (value === null || value === undefined) return <span className={styles.nullValue}>NULL</span>;
  if (value === '')
    return <span className={styles.nullValue}>{t('dataEditor.overview.empty')}</span>;
  if (isBytes(value)) {
    return (
      <span>
        <BytesSize value={value} />
      </span>
    );
  }
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  return <span>{text.length > SHOWN_CHARS ? `${text.slice(0, SHOWN_CHARS)}…` : text}</span>;
}

export function keyText(primaryKey: string[], key: Row): string {
  return primaryKey
    .map((column) => {
      const value = key[column];
      return `${column} = ${typeof value === 'string' ? value : JSON.stringify(value ?? null)}`;
    })
    .join(', ');
}

export function ChangeOverview({
  changes,
  primaryKey,
  testId,
}: {
  changes: Change[];
  primaryKey: string[];
  testId?: string;
}) {
  const { t } = useTranslation();
  const count = (op: Change['op']) => changes.filter((change) => change.op === op).length;
  return (
    <section
      className={styles.overview}
      aria-label={t('dataEditor.changesLabel')}
      data-testid={testId}
    >
      <p className={styles.overviewSummary}>
        {t('dataEditor.overview.summary', {
          updated: count('update'),
          inserted: count('insert'),
          deleted: count('delete'),
        })}
      </p>
      <ol>
        {changes.map((change, index) => {
          if (change.op === 'delete') {
            return (
              <li key={index} data-op="delete">
                <span className={styles.removedTitle}>
                  {t('dataEditor.overview.delete', {
                    key: keyText(primaryKey, change.key),
                    interpolation: { escapeValue: false },
                  })}
                </span>
              </li>
            );
          }
          const insert = change.op === 'insert';
          const entries = Object.entries(insert ? change.values : change.new);
          return (
            <li key={index} data-op={change.op}>
              <span className={insert ? styles.addedTitle : styles.changedTitle}>
                {insert
                  ? t('dataEditor.overview.insert', { row: index - count('update') + 1 })
                  : t('dataEditor.overview.update', {
                      key: keyText(primaryKey, change.key),
                      interpolation: { escapeValue: false },
                    })}
              </span>
              {entries.length > 0 ? (
                <table className={styles.overviewTable}>
                  <thead>
                    <tr>
                      <th scope="col">{t('dataEditor.overview.column')}</th>
                      {insert ? null : <th scope="col">{t('dataEditor.overview.before')}</th>}
                      <th scope="col">
                        {insert ? t('dataEditor.overview.value') : t('dataEditor.overview.after')}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {entries.map(([column, value]) => (
                      <tr key={column}>
                        <th scope="row">{column}</th>
                        {change.op === 'update' ? (
                          <td>
                            <ShownValue value={change.old[column]} />
                          </td>
                        ) : null}
                        <td>
                          <ShownValue value={value} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
              {insert ? (
                <span className={styles.overviewNote}>{t('dataEditor.overview.defaults')}</span>
              ) : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
