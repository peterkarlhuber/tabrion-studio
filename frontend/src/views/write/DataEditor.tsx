/**
 * The Data Editor (STU-026; 03 sections 2 D and 5.2, 08 section 4.6): a minimal grid to change, add and delete rows of
 * one table, the changes collected on the page, previewed by the server as a plan, confirmed by typing and saved in
 * one transaction. Rendered only while the server says the Data Editor flag is on (`GET /api/write/areas`); the page
 * never decides the flag, and the server refuses every route here while it is off. Gate G-S2 is not granted.
 *
 * 1. Unlock (D-03): this connection's write profile, its name typed; the server checks it and ends the unlock after
 *    15 minutes without use.
 * 2. Edit: the first page of rows from the viewer (the read connection); a cell's loaded value is the change's `old`
 *    value, every shown column of the row (review L-1 of tabrion #78). Text, numbers, booleans and NULL are editable;
 *    other values (JSON, bytes) and rows the viewer cut are read only (type editors: STU-XDE-1).
 * 3. Preview, confirm, save: the server's plan (no values in it), the text to type (D-09; the table's name when rows
 *    are deleted, D-10), then one run. A conflict or another row count rolls everything back and says so.
 *
 * The changes live in this component's memory only: never in browser storage, the query history or the drafts
 * (review N-3 of PR #109).
 */
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import {
  buildChanges,
  confirmPlan,
  discardPlan,
  editableValue,
  emptyEdits,
  executePlan,
  fetchEditorTable,
  fetchUnlocks,
  lockWrite,
  previewChangeset,
  sameValue,
  typedValue,
  unlockWrite,
  type Change,
  type EditorTable,
  type Edits,
  type PlanPreview,
  type PlanResult,
  type Row,
} from '../../api/dataEditor';
import type { Problem } from '../../api/problem';
import { fetchPage, type ViewerPage } from '../../api/viewer';
import { fetchWriteProfiles, type WriteProfileItem } from '../../api/write';
import { Button } from '../../components/ui/Button';
import clsx from '../../lib/clsx';
import connectionStyles from '../connections/Connections.module.css';
import { Field } from '../connections/Field';
import { problemText } from '../connections/messages';
import styles from './DataEditor.module.css';

/** The rows the editor loads (the first page of the viewer; paging is STU-XDE-1). */
export const EDITOR_ROWS = 100;

export function DataEditor({
  fetcher,
  connectionId,
  profileId,
  schema,
  name,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  /** The read profile of the connection (`user:<name>`): only write profiles coupled to it are offered. */
  profileId: string;
  schema: string;
  name: string;
}) {
  const { t } = useTranslation();
  const [unlocked, setUnlocked] = useState<boolean | null>(null);
  // Stable: the grid's loading effect depends on it.
  const locked = useCallback(() => setUnlocked(false), []);

  useEffect(() => {
    let alive = true;
    void fetchUnlocks(fetcher).then((list) => {
      if (alive) setUnlocked(list.some((item) => item.connectionId === connectionId));
    });
    return () => {
      alive = false;
    };
  }, [fetcher, connectionId]);

  if (unlocked === null) {
    return <p className={connectionStyles.hint}>{t('dataEditor.loading')}</p>;
  }
  if (!unlocked) {
    return (
      <UnlockForm
        fetcher={fetcher}
        connectionId={connectionId}
        profileId={profileId}
        onUnlocked={() => setUnlocked(true)}
      />
    );
  }
  return (
    <EditorGrid
      fetcher={fetcher}
      connectionId={connectionId}
      schema={schema}
      name={name}
      onLocked={locked}
    />
  );
}

function UnlockForm({
  fetcher,
  connectionId,
  profileId,
  onUnlocked,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  profileId: string;
  onUnlocked: () => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [profiles, setProfiles] = useState<WriteProfileItem[] | null>(null);
  const [chosen, setChosen] = useState('');
  const [typed, setTyped] = useState('');
  const [problem, setProblem] = useState<Problem | null>(null);

  useEffect(() => {
    let alive = true;
    void fetchWriteProfiles(fetcher).then((list) => {
      if (!alive) return;
      const own = list.filter(
        (item) => item.kind === 'write' && (profileId === '' || item.readProfileId === profileId),
      );
      setProfiles(own);
      setChosen(own[0]?.id ?? '');
    });
    return () => {
      alive = false;
    };
  }, [fetcher, profileId]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setProblem(null);
    const result = await unlockWrite(fetcher, connectionId, chosen, typed);
    if (result.ok) onUnlocked();
    else setProblem(result.problem);
  };

  if (profiles === null) return <p className={connectionStyles.hint}>{t('dataEditor.loading')}</p>;
  if (profiles.length === 0) {
    return (
      <p className={connectionStyles.hint} data-testid="data-editor-no-profile">
        {t('dataEditor.noProfile')}
      </p>
    );
  }
  const profile = profiles.find((item) => item.id === chosen);
  return (
    <form
      className={connectionStyles.form}
      onSubmit={(event) => void submit(event)}
      aria-labelledby={`${id}-title`}
      data-testid="data-editor-unlock"
    >
      <h3 id={`${id}-title`}>{t('dataEditor.unlock.title')}</h3>
      <p className={connectionStyles.hint}>{t('dataEditor.unlock.lead')}</p>
      {profiles.length > 1 ? (
        <Field label={t('dataEditor.unlock.profile')}>
          {(props) => (
            <select
              {...props}
              className={connectionStyles.input}
              value={chosen}
              onChange={(event) => setChosen(event.target.value)}
            >
              {profiles.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          )}
        </Field>
      ) : null}
      <Field label={t('dataEditor.unlock.type', { name: profile?.name ?? '' })}>
        {(props) => (
          <input
            {...props}
            className={connectionStyles.input}
            value={typed}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => setTyped(event.target.value)}
          />
        )}
      </Field>
      <div className={connectionStyles.actions}>
        <Button
          type="submit"
          variant="primary"
          disabled={profile === undefined || typed !== profile.name}
        >
          {t('dataEditor.unlock.submit')}
        </Button>
      </div>
      {problem !== null ? (
        <p role="alert" className={connectionStyles.error}>
          {problemText(t, problem)}
        </p>
      ) : null}
    </form>
  );
}

type Load =
  | { status: 'loading' }
  | { status: 'problem'; problem: Problem }
  | { status: 'ready'; table: EditorTable; page: ViewerPage };

type Stage =
  | { step: 'edit' }
  | { step: 'preview'; preview: PlanPreview; changes: Change[] }
  | { step: 'confirmed'; preview: PlanPreview; changes: Change[] };

/** The text of a loaded or typed value in a field (NULL is the empty field with the placeholder "NULL"). */
function fieldText(value: unknown): string {
  if (value === null || value === undefined) return '';
  return typeof value === 'string' ? value : JSON.stringify(value);
}

function EditorGrid({
  fetcher,
  connectionId,
  schema,
  name,
  onLocked,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  schema: string;
  name: string;
  onLocked: () => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [load, setLoad] = useState<Load>({ status: 'loading' });
  const [edits, setEdits] = useState<Edits>(emptyEdits);
  const [stage, setStage] = useState<Stage>({ step: 'edit' });
  const [typed, setTyped] = useState('');
  const [problem, setProblem] = useState<Problem | null>(null);
  const [saved, setSaved] = useState<PlanResult | null>(null);
  const [round, setRound] = useState(0);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const table = await fetchEditorTable(fetcher, connectionId, schema, name);
      if (!alive) return;
      if (!table.ok) {
        setLoad({ status: 'problem', problem: table.problem });
        if (table.problem.code === 'studio.write_locked') onLocked();
        return;
      }
      const page = await fetchPage(fetcher, connectionId, schema, name, {
        limit: EDITOR_ROWS,
        sort: null,
        desc: false,
        filters: [],
        after: null,
      });
      if (!alive) return;
      if (!page.ok) setLoad({ status: 'problem', problem: page.problem });
      else setLoad({ status: 'ready', table: table.value, page: page.value });
    })();
    return () => {
      alive = false;
    };
  }, [fetcher, connectionId, schema, name, round, onLocked]);

  const columns = useMemo(
    () => (load.status === 'ready' ? load.page.columns.map((column) => column.name) : []),
    [load],
  );
  const cut = useMemo(
    () => new Set(load.status === 'ready' ? load.page.truncatedCells.map(([row]) => row) : []),
    [load],
  );
  const changes = useMemo(
    () =>
      load.status === 'ready'
        ? buildChanges(load.table.primaryKey, columns, load.page.rows, edits)
        : [],
    [load, columns, edits],
  );

  const reload = useCallback(() => {
    setEdits(emptyEdits());
    setStage({ step: 'edit' });
    setTyped('');
    setLoad({ status: 'loading' });
    setRound((value) => value + 1);
  }, []);

  if (load.status === 'loading') {
    return <p className={connectionStyles.hint}>{t('dataEditor.loading')}</p>;
  }
  if (load.status === 'problem') {
    return (
      <p role="alert" className={connectionStyles.problem} data-testid="data-editor-problem">
        {problemText(t, load.problem)}
      </p>
    );
  }
  const { table, page } = load;
  const keyShown = table.primaryKey.every((key) => columns.includes(key));
  const editing = stage.step === 'edit';

  const setCell = (row: number, column: string, value: unknown) => {
    setSaved(null);
    setEdits((current) => {
      const cells = new Map(current.cells);
      const rowCells = new Map(cells.get(row) ?? []);
      rowCells.set(column, value);
      cells.set(row, rowCells);
      return { ...current, cells };
    });
  };
  const setNew = (index: number, column: string, value: unknown) => {
    setSaved(null);
    setEdits((current) => ({
      ...current,
      inserted: current.inserted.map((row, at) =>
        at === index ? { ...row, [column]: value } : row,
      ),
    }));
  };
  const toggleDelete = (row: number) => {
    setSaved(null);
    setEdits((current) => {
      const deleted = new Set(current.deleted);
      if (deleted.has(row)) deleted.delete(row);
      else deleted.add(row);
      return { ...current, deleted };
    });
  };

  const preview = async () => {
    setProblem(null);
    const result = await previewChangeset(fetcher, connectionId, table, columns, changes);
    if (result.ok) {
      setTyped('');
      setStage({ step: 'preview', preview: result.value, changes });
    } else {
      setProblem(result.problem);
    }
  };
  const back = () => {
    if (stage.step !== 'edit') void discardPlan(fetcher, stage.preview.planHash);
    setStage({ step: 'edit' });
    setTyped('');
  };
  const confirm = async (event: FormEvent) => {
    event.preventDefault();
    if (stage.step !== 'preview') return;
    setProblem(null);
    const result = await confirmPlan(fetcher, connectionId, stage.preview.planHash, typed);
    if (result.ok) setStage({ ...stage, step: 'confirmed' });
    else setProblem(result.problem);
  };
  const execute = async () => {
    if (stage.step !== 'confirmed') return;
    setProblem(null);
    const result = await executePlan(fetcher, connectionId, stage.preview.planHash);
    setStage({ step: 'edit' });
    setTyped('');
    if (result.ok) {
      setSaved(result.value);
      setEdits(emptyEdits());
      setLoad({ status: 'loading' });
      setRound((value) => value + 1);
    } else {
      setProblem(result.problem);
    }
  };
  const lock = async () => {
    await lockWrite(fetcher, connectionId);
    onLocked();
  };

  const keyText = (key: Row) => table.primaryKey.map((column) => fieldText(key[column])).join(', ');
  const changeText = (change: Change) => {
    if (change.op === 'insert') return t('dataEditor.change.insert');
    if (change.op === 'delete') {
      return t('dataEditor.change.delete', {
        key: keyText(change.key),
        interpolation: { escapeValue: false },
      });
    }
    return t('dataEditor.change.update', {
      key: keyText(change.key),
      columns: Object.keys(change.new).join(', '),
      interpolation: { escapeValue: false },
    });
  };

  const cellInput = (
    label: string,
    value: unknown,
    original: unknown,
    readOnly: boolean,
    onChange: (value: unknown) => void,
    placeholder: string,
  ) => (
    <span className={styles.cell}>
      <input
        aria-label={label}
        value={fieldText(value)}
        placeholder={placeholder}
        readOnly={readOnly || !editing}
        spellCheck={false}
        autoComplete="off"
        onChange={(event) => onChange(typedValue(original, event.target.value))}
      />
      {!readOnly && editing && value !== null ? (
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

  return (
    <div className={styles.editor} data-testid="data-editor">
      <div className={styles.summary}>
        <span role="status">{t('dataEditor.unlocked')}</span>
        <Button size="sm" onClick={() => void lock()}>
          {t('dataEditor.lock')}
        </Button>
      </div>
      {table.triggers.length > 0 || table.rules.length > 0 || table.rowSecurity ? (
        <p role="note" className={connectionStyles.warning}>
          {t('dataEditor.sideEffects')}
        </p>
      ) : null}
      {!keyShown ? (
        <p role="alert" className={connectionStyles.problem}>
          {t('dataEditor.keyNotShown')}
        </p>
      ) : null}
      {page.next !== null ? (
        <p className={connectionStyles.hint}>{t('dataEditor.firstRows', { count: EDITOR_ROWS })}</p>
      ) : null}
      <div className={styles.scroll}>
        <table
          className={styles.grid}
          aria-labelledby={`${id}-caption`}
          data-testid="data-editor-grid"
        >
          <caption id={`${id}-caption`} className={connectionStyles.hint}>
            {t('dataEditor.caption', {
              name: `${table.schema}.${table.table}`,
              interpolation: { escapeValue: false },
            })}
          </caption>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column} scope="col">
                  {column}
                  {table.primaryKey.includes(column) ? ` (${t('dataEditor.key')})` : ''}
                </th>
              ))}
              <th scope="col">{t('dataEditor.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {page.rows.map((row, index) => {
              const deleted = edits.deleted.has(index);
              const locked = cut.has(index) || !keyShown;
              return (
                <tr
                  key={`row-${index}`}
                  className={clsx(deleted && styles.removed)}
                  data-row={index}
                >
                  {columns.map((column, at) => {
                    const original = row[at] ?? null;
                    const value = edits.cells.get(index)?.has(column)
                      ? edits.cells.get(index)?.get(column)
                      : original;
                    const readOnly = locked || deleted || !editableValue(original);
                    return (
                      <td
                        key={column}
                        className={clsx(!sameValue(value, original) && styles.changed)}
                      >
                        {cellInput(
                          t('dataEditor.cell', {
                            row: index + 1,
                            column,
                            interpolation: { escapeValue: false },
                          }),
                          value,
                          original,
                          readOnly,
                          (next) => setCell(index, column, next),
                          'NULL',
                        )}
                      </td>
                    );
                  })}
                  <td>
                    <Button
                      size="sm"
                      variant={deleted ? 'secondary' : 'dangerOutline'}
                      disabled={locked || !editing}
                      onClick={() => toggleDelete(index)}
                    >
                      {deleted ? t('dataEditor.undoDelete') : t('dataEditor.delete')}
                    </Button>
                  </td>
                </tr>
              );
            })}
            {edits.inserted.map((row, index) => (
              <tr key={`new-${index}`} className={styles.added} data-new-row={index}>
                {columns.map((column) => (
                  <td key={column}>
                    {cellInput(
                      t('dataEditor.newCell', {
                        row: index + 1,
                        column,
                        interpolation: { escapeValue: false },
                      }),
                      column in row ? row[column] : null,
                      '',
                      false,
                      (next) => setNew(index, column, next),
                      column in row ? 'NULL' : t('dataEditor.default'),
                    )}
                  </td>
                ))}
                <td>
                  <Button
                    size="sm"
                    disabled={!editing}
                    onClick={() =>
                      setEdits((current) => ({
                        ...current,
                        inserted: current.inserted.filter((_, at) => at !== index),
                      }))
                    }
                  >
                    {t('dataEditor.removeNew')}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className={styles.summary}>
        <Button
          disabled={!editing || !keyShown}
          onClick={() =>
            setEdits((current) => ({ ...current, inserted: [...current.inserted, {}] }))
          }
        >
          {t('dataEditor.addRow')}
        </Button>
        <span data-testid="data-editor-pending">
          {t('dataEditor.pending', { count: changes.length })}
        </span>
        <Button disabled={!editing || changes.length === 0} onClick={reload}>
          {t('dataEditor.discard')}
        </Button>
        <Button
          variant="primary"
          disabled={!editing || changes.length === 0 || changes.length > table.maxChanges}
          onClick={() => void preview()}
        >
          {t('dataEditor.preview')}
        </Button>
      </div>
      {changes.length > 0 && editing ? (
        <ol
          className={styles.changes}
          aria-label={t('dataEditor.changesLabel')}
          data-testid="data-editor-changes"
        >
          {changes.map((change, index) => (
            <li key={index}>{changeText(change)}</li>
          ))}
        </ol>
      ) : null}
      {stage.step !== 'edit' ? (
        <form
          className={connectionStyles.form}
          onSubmit={(event) => void confirm(event)}
          aria-labelledby={`${id}-review`}
          data-testid="data-editor-review"
        >
          <h3 id={`${id}-review`}>{t('dataEditor.review.title')}</h3>
          <ol className={styles.changes}>
            {stage.changes.map((change, index) => (
              <li key={index}>{changeText(change)}</li>
            ))}
          </ol>
          <p>{t('dataEditor.review.oneTransaction')}</p>
          {stage.preview.triggers.length > 0 || stage.preview.rules.length > 0 ? (
            <p role="note" className={connectionStyles.warning}>
              {t('dataEditor.review.sideEffects', {
                names: [...stage.preview.triggers, ...stage.preview.rules].join(', '),
                interpolation: { escapeValue: false },
              })}
            </p>
          ) : null}
          {stage.step === 'preview' ? (
            <>
              <Field
                label={t('dataEditor.review.type', {
                  text: stage.preview.confirmText,
                  interpolation: { escapeValue: false },
                })}
              >
                {(props) => (
                  <input
                    {...props}
                    className={connectionStyles.input}
                    value={typed}
                    autoComplete="off"
                    spellCheck={false}
                    onChange={(event) => setTyped(event.target.value)}
                  />
                )}
              </Field>
              <div className={connectionStyles.actions}>
                <Button onClick={back}>{t('dataEditor.review.back')}</Button>
                <Button
                  type="submit"
                  variant={stage.preview.destructive ? 'danger' : 'primary'}
                  disabled={typed !== stage.preview.confirmText}
                >
                  {t('dataEditor.review.confirm')}
                </Button>
              </div>
            </>
          ) : (
            <div className={connectionStyles.actions}>
              <Button onClick={back}>{t('dataEditor.review.back')}</Button>
              <Button
                variant={stage.preview.destructive ? 'danger' : 'primary'}
                onClick={() => void execute()}
                data-testid="data-editor-execute"
              >
                {t('dataEditor.review.execute')}
              </Button>
            </div>
          )}
        </form>
      ) : null}
      {saved !== null ? (
        <p role="status" className={connectionStyles.status} data-testid="data-editor-saved">
          {t('dataEditor.saved', {
            updated: saved.rows.updated ?? 0,
            inserted: saved.rows.inserted ?? 0,
            deleted: saved.rows.deleted ?? 0,
          })}
          {saved.auditPending ? ` ${t('dataEditor.auditPending')}` : ''}
        </p>
      ) : null}
      {problem !== null ? (
        <div role="alert" className={connectionStyles.problem} data-testid="data-editor-error">
          <span>{problemText(t, problem)}</span>
          {[
            'studio.data_editor_conflict',
            'studio.data_editor_rowcount',
            'studio.plan_drift',
          ].includes(problem.code) ? (
            <Button size="sm" onClick={reload}>
              {t('dataEditor.reload')}
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
