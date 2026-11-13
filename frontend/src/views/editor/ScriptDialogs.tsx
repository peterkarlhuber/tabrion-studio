/**
 * Opening and saving `.sql` scripts of the project folder (STU-020; 03 section 2 C "SQL-Skripte öffnen/speichern
 * (Projektordner, nur `.sql`)"). Paths are relative to the project folder and are checked again by the server; the
 * page shows them as text (`lib/visible`). Each dialog keeps the focus rules of 08 section 6: the first focus is on
 * a control that changes nothing (the filter or the path field), Escape cancels.
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { Problem } from '../../api/problem';
import { fetchScripts, validScriptPath, type ScriptList } from '../../api/scripts';
import { Dialog } from '../../components/ui/Dialog';
import { visible } from '../../lib/visible';
import connectionStyles from '../connections/Connections.module.css';
import { problemText } from '../connections/messages';
import styles from './Editor.module.css';

/** The shown list is cut here; the filter finds the rest. */
const SHOWN_SCRIPTS = 200;

export function OpenScriptDialog({
  fetcher,
  problem,
  onOpen,
  onCancel,
}: {
  fetcher: typeof fetch;
  /** Why the last chosen script did not open. */
  problem: Problem | null;
  onOpen: (path: string) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const id = useId().replace(/:/g, '');
  const filterRef = useRef<HTMLInputElement>(null);
  const [list, setList] = useState<ScriptList | null | 'loading'>('loading');
  const [listProblem, setListProblem] = useState<Problem | null>(null);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    // The filter changes nothing: it takes the first focus (after the dialog's own).
    filterRef.current?.focus();
    let active = true;
    void fetchScripts(fetcher).then((result) => {
      if (!active) return;
      if (result.ok) setList(result.value);
      else {
        setList(null);
        setListProblem(result.problem);
      }
    });
    return () => {
      active = false;
    };
  }, [fetcher]);

  const matching = useMemo(() => {
    if (list === null || list === 'loading') return [];
    const needle = filter.trim().toLowerCase();
    return list.scripts.filter((item) => needle === '' || item.path.toLowerCase().includes(needle));
  }, [filter, list]);
  const shown = matching.slice(0, SHOWN_SCRIPTS);

  return (
    <Dialog
      open
      testId="dialog-open-script"
      title={t('editor.scripts.openTitle')}
      description={t('editor.scripts.openBody')}
      initialFocus="cancel"
      onCancel={onCancel}
      actions={[{ id: 'cancel', label: t('dialog.cancel'), onSelect: onCancel }]}
    >
      <label className={connectionStyles.field} htmlFor={`${id}-filter`}>
        <span className={connectionStyles.label}>{t('editor.scripts.filter')}</span>
        <input
          ref={filterRef}
          id={`${id}-filter`}
          type="search"
          className={connectionStyles.input}
          value={filter}
          maxLength={200}
          onChange={(event) => setFilter(event.target.value)}
          data-testid="script-filter"
        />
      </label>
      {problem !== null ? (
        <p role="alert" className={connectionStyles.problem} data-testid="script-open-problem">
          {problemText(t, problem)}
        </p>
      ) : null}
      {list === 'loading' ? (
        <p className={connectionStyles.hint}>{t('editor.scripts.loading')}</p>
      ) : list === null ? (
        <p role="alert" className={connectionStyles.problem}>
          {listProblem === null ? t('editor.scripts.unavailable') : problemText(t, listProblem)}
        </p>
      ) : shown.length === 0 ? (
        <p className={connectionStyles.hint} data-testid="script-none">
          {list.scripts.length === 0 ? t('editor.scripts.none') : t('editor.scripts.noMatch')}
        </p>
      ) : (
        <ul
          className={styles.scriptList}
          aria-label={t('editor.scripts.list')}
          data-testid="script-list"
        >
          {shown.map((item) => (
            <li key={item.path}>
              <button
                type="button"
                className={styles.scriptItem}
                onClick={() => onOpen(item.path)}
                data-testid="script-item"
              >
                {visible(item.path)}
              </button>
            </li>
          ))}
        </ul>
      )}
      {list !== null && list !== 'loading' && (list.truncated || matching.length > shown.length) ? (
        <p className={connectionStyles.hint}>{t('editor.scripts.more')}</p>
      ) : null}
      {list !== null && list !== 'loading' && list.skippedLinks > 0 ? (
        <p className={connectionStyles.hint} data-testid="script-skipped-links">
          {t('editor.scripts.skippedLinks', { count: list.skippedLinks })}
        </p>
      ) : null}
      {list !== null && list !== 'loading' && list.skippedInvalid > 0 ? (
        <p className={connectionStyles.hint} data-testid="script-skipped-invalid">
          {t('editor.scripts.skippedInvalid', { count: list.skippedInvalid })}
        </p>
      ) : null}
    </Dialog>
  );
}

export function SaveScriptDialog({
  initialPath,
  problem,
  onSave,
  onDownload,
  onCancel,
}: {
  initialPath: string;
  /** Why the last save was refused. */
  problem: Problem | null;
  onSave: (path: string) => void;
  /** Save as a download instead (the browser's download folder; outside the project). */
  onDownload: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const id = useId().replace(/:/g, '');
  const field = useRef<HTMLInputElement>(null);
  const [path, setPath] = useState(initialPath);
  const [touched, setTouched] = useState(false);
  const valid = validScriptPath(path.trim());

  useEffect(() => {
    field.current?.focus();
    field.current?.select();
  }, []);

  const save = () => {
    setTouched(true);
    if (valid) onSave(path.trim());
  };

  return (
    <Dialog
      open
      testId="dialog-save-script"
      title={t('editor.scripts.saveTitle')}
      description={t('editor.scripts.saveBody')}
      initialFocus="cancel"
      onCancel={onCancel}
      actions={[
        { id: 'cancel', label: t('dialog.cancel'), onSelect: onCancel },
        { id: 'download', label: t('editor.scripts.download'), onSelect: onDownload },
        { id: 'save', label: t('editor.scripts.save'), variant: 'primary', onSelect: save },
      ]}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          save();
        }}
      >
        <label className={connectionStyles.field} htmlFor={`${id}-path`}>
          <span className={connectionStyles.label}>{t('editor.scripts.path')}</span>
          <input
            ref={field}
            id={`${id}-path`}
            className={connectionStyles.input}
            value={path}
            maxLength={512}
            spellCheck={false}
            autoComplete="off"
            aria-invalid={touched && !valid ? true : undefined}
            aria-describedby={`${id}-path-hint`}
            onChange={(event) => setPath(event.target.value)}
            data-testid="script-path"
          />
        </label>
        <p
          id={`${id}-path-hint`}
          className={touched && !valid ? connectionStyles.problem : connectionStyles.hint}
        >
          {touched && !valid ? t('editor.scripts.pathInvalid') : t('editor.scripts.pathHint')}
        </p>
      </form>
      {problem !== null ? (
        <p role="alert" className={connectionStyles.problem} data-testid="script-save-problem">
          {problemText(t, problem)}
        </p>
      ) : null}
    </Dialog>
  );
}

/** The script changed on disk, or appeared, since the page read it: overwrite only on request. */
export function OverwriteScriptDialog({
  path,
  reason,
  onOverwrite,
  onCancel,
}: {
  path: string;
  reason: 'stale' | 'exists';
  onOverwrite: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open
      testId="dialog-overwrite-script"
      title={t('editor.scripts.overwriteTitle')}
      description={t(
        reason === 'stale' ? 'editor.scripts.staleBody' : 'editor.scripts.existsBody',
        {
          path: visible(path),
          interpolation: { escapeValue: false },
        },
      )}
      initialFocus="cancel"
      onCancel={onCancel}
      actions={[
        { id: 'cancel', label: t('dialog.cancel'), onSelect: onCancel },
        {
          id: 'overwrite',
          label: t('editor.scripts.overwrite'),
          variant: 'dangerOutline',
          onSelect: onOverwrite,
        },
      ]}
    />
  );
}
