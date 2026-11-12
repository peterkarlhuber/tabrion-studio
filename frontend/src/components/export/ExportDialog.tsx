/**
 * The export dialog (STU-021 in the page, TD-043; 08 section 5 "Export": result → ⤓ CSV/JSON → dialog (the fetched
 * rows / query again up to a limit) → download, with a hint when the export is cut).
 *
 * - **Source:** what the caller passes (`ExportSource`): a statement of the SQL editor with its parameters, or a table
 *   of the data viewer (STU-013). The export runs on the caller's open connection; the caller asks D-02 (the shared
 *   `useConnect` guard) before it opens the dialog, and the dialog never opens a connection itself.
 * - **Rows:** "the rows of the result" (as many rows as the page fetched; the server runs the statement again, so the
 *   file holds the rows as they are now) or "up to" a limit of 1 to 1 000 000, the server's maximum. A result that
 *   was cut says that the export of its rows is cut too. A JSON file that the server cut says so after the download;
 *   a CSV file cannot mark it, which the dialog says before.
 * - **CSV formulas:** on by default (the server's protection against CSV injection); JSON is data and never changed.
 * - **Running:** the bytes received are announced; Cancel (or Escape) aborts the request with an `AbortController`,
 *   which makes the server close the export, and nothing is saved. Closing the dialog or leaving the page aborts too.
 *   The file is saved only when it arrived whole, under the server's file name when it has the expected form.
 * - **Problems** are the page's en/de texts; the caller can pass its own wording for statement problems.
 */
import type { TFunction } from 'i18next';
import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  downloadExport,
  EXPORT_CANCELED,
  MAX_EXPORT_ROWS,
  type ExportFormat,
  type ExportSource,
} from '../../api/exports';
import type { Problem } from '../../api/problem';
import { saveFile, type SaveFile } from '../../lib/download';
import { problemText } from '../../views/connections/messages';
import connectionStyles from '../../views/connections/Connections.module.css';
import { Dialog } from '../ui/Dialog';
import styles from './ExportDialog.module.css';

export interface ExportDialogProps {
  fetcher: typeof fetch;
  /** The open connection the source belongs to (it passed D-02). */
  connectionId: string;
  source: ExportSource;
  /** What is exported, as plain text the caller made safe ("Result 1 of Query 1", "Table public.parts"). */
  subject: string;
  /** The rows the page fetched of this result; null for a table (nothing fetched). */
  fetchedRows: number | null;
  /** The result was cut at its row or size limit: more rows exist than were fetched. */
  capped?: boolean;
  /** The text of a problem; by default the page's general problem texts. */
  describeProblem?: (t: TFunction, problem: Problem) => string;
  save?: SaveFile;
  /** After a saved export, with the sentence for the page's status line. */
  onDone: (message: string) => void;
  onClose: () => void;
}

type RowsMode = 'fetched' | 'limit';

/** The byte count for people: B, KB or MB, one decimal. */
export function formatBytes(bytes: number, locale: string): string {
  const number = (value: number, digits: number) =>
    new Intl.NumberFormat(locale, { maximumFractionDigits: digits }).format(value);
  if (bytes < 1024) return `${number(bytes, 0)} B`;
  if (bytes < 1024 * 1024) return `${number(bytes / 1024, 1)} KB`;
  return `${number(bytes / (1024 * 1024), 1)} MB`;
}

/** The row limit typed in the dialog, or null when it is not a whole number from 1 to `MAX_EXPORT_ROWS`. */
export function parseRowLimit(text: string): number | null {
  const trimmed = text.trim();
  if (!/^\d{1,7}$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return value >= 1 && value <= MAX_EXPORT_ROWS ? value : null;
}

export function exportProblemText(t: TFunction, problem: Problem): string {
  switch (problem.code) {
    case 'studio.export_invalid':
      return t('export.problems.invalid');
    case 'studio.export_busy':
      return t('export.problems.busy');
    case 'studio.export_stopped':
      return t('export.problems.stopped');
    case 'studio.export_incomplete':
      return t('export.problems.incomplete');
    case 'studio.connection_not_found':
    case 'studio.connection_stale':
      return t('export.problems.connectionGone');
    default:
      return problemText(t, problem);
  }
}

export function ExportDialog({
  fetcher,
  connectionId,
  source,
  subject,
  fetchedRows,
  capped = false,
  describeProblem,
  save = saveFile,
  onDone,
  onClose,
}: ExportDialogProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const id = useId();
  const canFetched = fetchedRows !== null && fetchedRows > 0;
  const [format, setFormat] = useState<ExportFormat>('csv');
  const [mode, setMode] = useState<RowsMode>(canFetched ? 'fetched' : 'limit');
  const [limit, setLimit] = useState(String(MAX_EXPORT_ROWS));
  const [limitInvalid, setLimitInvalid] = useState(false);
  const [escape, setEscape] = useState(true);
  const [running, setRunningState] = useState(false);
  /** Whether an export runs, as of now: a second press before the next render starts nothing. */
  const runningNow = useRef(false);
  const setRunning = (next: boolean) => {
    runningNow.current = next;
    setRunningState(next);
  };
  const [progress, setProgress] = useState('');
  const [problem, setProblem] = useState<Problem | null>(null);
  const abort = useRef<AbortController | null>(null);
  const limitRef = useRef<HTMLInputElement>(null);

  // Closing the dialog or leaving the page stops a running export (the server closes it when the request goes).
  useEffect(
    () => () => {
      abort.current?.abort();
      abort.current = null;
    },
    [],
  );

  const describe = (value: Problem) =>
    describeProblem ? describeProblem(t, value) : exportProblemText(t, value);

  const start = async () => {
    if (runningNow.current) return;
    const maxRows =
      mode === 'fetched' && canFetched ? (fetchedRows as number) : parseRowLimit(limit);
    if (maxRows === null) {
      setLimitInvalid(true);
      limitRef.current?.focus();
      return;
    }
    setLimitInvalid(false);
    setProblem(null);
    setRunning(true);
    setProgress(t('export.starting'));
    const controller = new AbortController();
    abort.current = controller;
    const result = await downloadExport(
      fetcher,
      { connectionId, format, source, maxRows, escapeFormulas: escape },
      {
        signal: controller.signal,
        onProgress: (bytes) =>
          setProgress(t('export.received', { size: formatBytes(bytes, locale) })),
      },
    );
    if (abort.current !== controller) return; // closed or unmounted meanwhile: nothing more to show
    abort.current = null;
    setRunning(false);
    if (!result.ok) {
      if (result.problem.code === EXPORT_CANCELED.code) {
        setProgress(t('export.canceled'));
      } else {
        setProgress('');
        setProblem(result.problem);
      }
      return;
    }
    const file = result.value;
    save(file.blob, file.filename);
    const size = formatBytes(file.bytes, locale);
    onDone(
      file.truncated === true
        ? t('export.doneCut', {
            filename: file.filename,
            size,
            rows: new Intl.NumberFormat(locale).format(maxRows),
          })
        : t('export.done', { filename: file.filename, size }),
    );
  };

  const cancel = () => {
    if (runningNow.current) {
      abort.current?.abort();
      return;
    }
    onClose();
  };

  const cutNote =
    mode === 'fetched' && capped
      ? t('export.cappedFetched')
      : format === 'csv'
        ? t('export.csvCut')
        : t('export.jsonCut');

  return (
    <Dialog
      open
      testId="dialog-export"
      title={t('export.title')}
      description={t('export.description', { subject })}
      initialFocus="cancel"
      onCancel={cancel}
      actions={[
        {
          id: 'cancel',
          label: running ? t('export.cancel') : t('dialog.cancel'),
          onSelect: cancel,
        },
        {
          id: 'export',
          label: t('export.start'),
          variant: 'primary',
          disabled: running,
          onSelect: () => void start(),
        },
      ]}
    >
      <div className={styles.form}>
        <fieldset className={styles.group} disabled={running}>
          <legend className={styles.legend}>{t('export.format')}</legend>
          {(['csv', 'json'] as const).map((value) => (
            <label key={value} className={styles.choice}>
              <input
                type="radio"
                name={`${id}-format`}
                value={value}
                checked={format === value}
                onChange={() => setFormat(value)}
              />
              <span>{t(`export.formats.${value}`)}</span>
            </label>
          ))}
        </fieldset>
        <fieldset className={styles.group} disabled={running}>
          <legend className={styles.legend}>{t('export.rows')}</legend>
          {canFetched ? (
            <label className={styles.choice}>
              <input
                type="radio"
                name={`${id}-rows`}
                value="fetched"
                checked={mode === 'fetched'}
                onChange={() => setMode('fetched')}
              />
              <span>
                {t('export.fetched', {
                  count: fetchedRows as number,
                  rows: new Intl.NumberFormat(locale).format(fetchedRows as number),
                })}
              </span>
            </label>
          ) : null}
          <div className={styles.limitRow}>
            <label className={styles.choice}>
              <input
                type="radio"
                name={`${id}-rows`}
                value="limit"
                checked={mode === 'limit'}
                onChange={() => setMode('limit')}
              />
              <span>{t('export.upTo')}</span>
            </label>
            <input
              ref={limitRef}
              id={`${id}-limit`}
              className={connectionStyles.input}
              type="text"
              inputMode="numeric"
              aria-label={t('export.limitLabel')}
              aria-invalid={limitInvalid || undefined}
              aria-describedby={limitInvalid ? `${id}-limit-error` : `${id}-limit-hint`}
              value={limit}
              onFocus={() => setMode('limit')}
              onChange={(event) => {
                setLimit(event.target.value);
                setMode('limit');
                setLimitInvalid(false);
              }}
              data-testid="export-limit"
            />
          </div>
          <p id={`${id}-limit-hint`} className={connectionStyles.hint}>
            {t('export.limitHint', { max: new Intl.NumberFormat(locale).format(MAX_EXPORT_ROWS) })}
          </p>
          {limitInvalid ? (
            <p id={`${id}-limit-error`} className={connectionStyles.problem}>
              {t('export.limitInvalid', {
                max: new Intl.NumberFormat(locale).format(MAX_EXPORT_ROWS),
              })}
            </p>
          ) : null}
        </fieldset>
        {format === 'csv' ? (
          <label className={styles.choice}>
            <input
              type="checkbox"
              checked={escape}
              disabled={running}
              onChange={(event) => setEscape(event.target.checked)}
            />
            <span>{t('export.escape')}</span>
          </label>
        ) : null}
        <p className={connectionStyles.hint} data-testid="export-note">
          {source.kind === 'sql' ? `${t('export.again')} ` : ''}
          {cutNote}
        </p>
        <p role="status" className={styles.progress} data-testid="export-progress">
          {progress}
        </p>
        {problem !== null ? (
          <p role="alert" className={connectionStyles.problem} data-testid="export-problem">
            {describe(problem)}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
