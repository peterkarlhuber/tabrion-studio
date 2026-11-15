/**
 * The Data Editor's cell editors (STU-XDE-1), chosen by the column's type (`cellValues.ts`):
 *
 * - boolean and enum: a choice of the values (and NULL where the column takes it; DEFAULT in a new row);
 * - JSON and arrays: a text field that takes JSON (`{"a": 1}`, `[1, 2]`); a JSON `null` is refused, NULL is the button;
 * - every other kind: a text field whose text is kept as typed and checked for the column when the changeset is built
 *   (numbers, decimals, dates and times with the format in the header, UUIDs, text with its length);
 * - generated and identity ALWAYS columns, bytes, and values the viewer cut: read only.
 *
 * NULL is never the empty text: an empty field is the empty text, and the NULL button (only where the column takes
 * NULL) sets NULL, shown as the placeholder "NULL". A cell with an issue is `aria-invalid` and names the issue.
 */
import { useTranslation } from 'react-i18next';

import { editorKind, fieldText, Typed, type ColumnType } from '../../api/cellValues';
import { Button } from '../../components/ui/Button';
import styles from './DataEditor.module.css';

const NULL_OPTION = '\u0000null';
const DEFAULT_OPTION = '\u0000default';

export function CellEditor({
  column,
  label,
  value,
  original,
  unset = false,
  disabled,
  issueId,
  onChange,
  onUnset,
}: {
  column: ColumnType;
  label: string;
  /** The cell's current value: the loaded one, a choice, `null` or `Typed` text. */
  value: unknown;
  original: unknown;
  /** A new row's cell that is left to the column's default. */
  unset?: boolean;
  disabled: boolean;
  /** The id of the text that names the cell's issue, when it has one. */
  issueId: string | null;
  onChange: (value: unknown) => void;
  onUnset?: () => void;
}) {
  const { t } = useTranslation();
  const kind = editorKind(column, original);
  const invalid = issueId !== null ? { 'aria-invalid': true, 'aria-describedby': issueId } : {};
  const placeholder = unset ? t('dataEditor.default') : 'NULL';

  if (kind === 'boolean' || kind === 'enum') {
    const choices = kind === 'boolean' ? ['true', 'false'] : column.values;
    const current = unset
      ? DEFAULT_OPTION
      : value === null
        ? NULL_OPTION
        : typeof value === 'boolean'
          ? String(value)
          : fieldText(value);
    return (
      <span className={styles.cell}>
        <select
          aria-label={label}
          value={current}
          disabled={disabled}
          onChange={(event) => {
            const chosen = event.target.value;
            if (chosen === DEFAULT_OPTION) onUnset?.();
            else if (chosen === NULL_OPTION) onChange(null);
            else onChange(kind === 'boolean' ? chosen === 'true' : chosen);
          }}
          {...invalid}
        >
          {unset || onUnset !== undefined ? (
            <option value={DEFAULT_OPTION}>{t('dataEditor.default')}</option>
          ) : null}
          {column.nullable || value === null ? <option value={NULL_OPTION}>NULL</option> : null}
          {/* A loaded value that is not among the choices (a label the page does not know) stays shown. */}
          {[
            ...choices,
            ...(choices.includes(current) || current.startsWith('\u0000') ? [] : [current]),
          ].map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
      </span>
    );
  }

  const readOnly = kind === 'readOnly' || disabled;
  return (
    <span className={styles.cell}>
      <input
        aria-label={label}
        value={unset ? '' : fieldText(value)}
        placeholder={value === null || unset ? placeholder : undefined}
        readOnly={readOnly}
        spellCheck={false}
        autoComplete="off"
        className={column.kind === 'json' || column.array ? styles.code : undefined}
        onChange={(event) => onChange(new Typed(event.target.value))}
        {...invalid}
      />
      {!readOnly && column.nullable && (value !== null || unset) ? (
        <Button
          size="sm"
          variant="ghost"
          aria-label={t('dataEditor.setNull', { label })}
          onClick={() => onChange(null)}
        >
          {t('dataEditor.null')}
        </Button>
      ) : null}
    </span>
  );
}
