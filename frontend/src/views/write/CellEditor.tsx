/**
 * The Data Editor's cell editors (STU-XDE-1), chosen by the column's type (`cellValues.ts`):
 *
 * - boolean and enum: a choice of the values (and NULL where the column takes it; DEFAULT in a new row);
 * - JSON and arrays: a text field that takes JSON (`{"a": 1}`, `[1, 2]`); a JSON `null` is refused, NULL is the button;
 * - every other kind: a text field whose text is kept as typed and checked for the column when the changeset is built
 *   (numbers, decimals, dates and times with the format in the header, UUIDs, text with its length);
 * - bytes (G-S2 6A): replaced by the content of a file the user picks, never typed; the cell shows the size, a file
 *   larger than `MAX_BYTES_VALUE` is kept as `FileTooLarge` (never read) and marked `value.too_large`;
 * - generated and identity ALWAYS columns, and values the viewer cut: read only.
 *
 * NULL is never the empty text: an empty field is the empty text, and the NULL button (only where the column takes
 * NULL) sets NULL, shown as the placeholder "NULL". A cell with an issue is `aria-invalid` and names the issue.
 */
import { useId, useRef } from 'react';
import { useTranslation } from 'react-i18next';

import {
  bytesSize,
  bytesValue,
  editorKind,
  fieldText,
  FileTooLarge,
  isBytes,
  MAX_BYTES_VALUE,
  Typed,
  type ColumnType,
} from '../../api/cellValues';
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

  if (kind === 'file') {
    return (
      <BytesEditor
        column={column}
        label={label}
        value={value}
        unset={unset}
        disabled={disabled}
        issueId={issueId}
        onChange={onChange}
      />
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

/** The size of a bytes value as the cell shows it (the bytes themselves are never shown). */
export function BytesSize({ value }: { value: unknown }) {
  const { t } = useTranslation();
  if (value instanceof FileTooLarge) return <>{t('dataEditor.bytes', { count: value.size })}</>;
  if (isBytes(value)) {
    return <>{t('dataEditor.bytes', { count: bytesSize(value as { base64: string }) })}</>;
  }
  return <>{fieldText(value)}</>;
}

/**
 * A bytes cell (G-S2 6A): its size, a button that opens the file chooser, and NULL where the column takes it. The
 * picked file's content becomes the new value; a file above the limit is not read.
 */
function BytesEditor({
  column,
  label,
  value,
  unset,
  disabled,
  issueId,
  onChange,
}: {
  column: ColumnType;
  label: string;
  value: unknown;
  unset: boolean;
  disabled: boolean;
  issueId: string | null;
  onChange: (value: unknown) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const pick = (file: File | undefined) => {
    if (file === undefined) return;
    if (file.size > MAX_BYTES_VALUE) {
      onChange(new FileTooLarge(file.size));
      return;
    }
    void file.arrayBuffer().then((content) => onChange(bytesValue(content)));
  };
  return (
    <span className={styles.cell}>
      <span id={`${id}-value`} className={value === null || unset ? styles.nullValue : undefined}>
        {unset ? t('dataEditor.default') : value === null ? 'NULL' : <BytesSize value={value} />}
      </span>
      <input
        ref={input}
        type="file"
        hidden
        tabIndex={-1}
        aria-label={label}
        onChange={(event) => {
          pick(event.target.files?.[0]);
          event.target.value = ''; // the same file may be picked again later
        }}
      />
      <Button
        size="sm"
        disabled={disabled}
        aria-label={t('dataEditor.replaceFile', { label })}
        aria-describedby={[`${id}-value`, issueId].filter(Boolean).join(' ')}
        aria-invalid={issueId !== null ? true : undefined}
        onClick={() => input.current?.click()}
      >
        {t('dataEditor.replace')}
      </Button>
      {!disabled && column.nullable && (value !== null || unset) ? (
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
