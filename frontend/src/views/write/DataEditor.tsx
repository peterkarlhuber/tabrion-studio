/**
 * The Data Editor (STU-026; 03 sections 2 D and 5.2, 08 section 4.6): a minimal grid to change, add and delete rows of
 * one table, the changes collected on the page, previewed by the server as a plan, confirmed by typing and saved in
 * one transaction. Rendered only while the server says the Data Editor flag is on (`GET /api/write/areas`); the page
 * never decides the flag, and the server refuses every route here while it is off. Gate G-S2 is not granted.
 *
 * 1. Unlock (D-03): this connection's write profile, its name typed; the server checks it and ends the unlock after
 *    15 minutes without use.
 * 2. Edit: the rows from the viewer (the read connection), page after page ("Load more rows", G-S2 6A; at most
 *    `MAX_EDITOR_ROWS`): a page is appended to the rows loaded before, so every row keeps its index and its edits
 *    (`appendRows`), and a reload drops every edit and starts at the first page again. A cell's loaded value is the
 *    change's `old` value, every shown column of the row (review L-1 of tabrion #78). Each cell has the editor of its
 *    column's type (STU-XDE-1, `CellEditor.tsx`; bytes are replaced by a picked file, G-S2 6A), and every changed
 *    value is checked for its column on the page (`cellValues.ts`): a cell with an issue is marked and named, and the
 *    preview waits until none is left. Rows the viewer cut are read only.
 * 3. Review: the change overview (`ChangeOverview.tsx`) lists every changed row with its columns before and after,
 *    the new rows and the deleted ones. Preview, confirm, save: the server checks every value again and makes the
 *    plan (no values in it; a refused value marks its cell), the text to type (D-09; the table's name when rows are
 *    deleted, D-10), then one run. A conflict or another row count rolls everything back and says so; a change the
 *    database refuses for a rule of the table (`studio.data_editor_constraint`, G-S2 6A) rolls everything back too
 *    and is marked at its cell, or at its row where the database named no single column.
 *
 * The changes live in this component's memory only: never in browser storage, the query history or the drafts
 * (review N-3 of PR #109).
 */
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { MAX_BYTES_VALUE, typeLabel, untyped } from '../../api/cellValues';
import {
  appendRows,
  buildChangeset,
  changesetBytes,
  confirmPlan,
  discardPlan,
  emptyEdits,
  executePlan,
  fetchEditorTable,
  fetchUnlocks,
  firstRows,
  lockWrite,
  MAX_CHANGESET_BYTES,
  previewChangeset,
  resolveEdits,
  sameValue,
  serverIssues,
  unlockWrite,
  type CellIssue,
  type CellRef,
  type Change,
  type EditorTable,
  type Edits,
  type LoadedRows,
  type PlanPreview,
  type PlanResult,
} from '../../api/dataEditor';
import type { Problem } from '../../api/problem';
import { fetchPage } from '../../api/viewer';
import { fetchWriteProfiles, type WriteProfileItem } from '../../api/write';
import { Button } from '../../components/ui/Button';
import clsx from '../../lib/clsx';
import connectionStyles from '../connections/Connections.module.css';
import { Field } from '../connections/Field';
import { problemText } from '../connections/messages';
import { CellEditor } from './CellEditor';
import { ChangeOverview } from './ChangeOverview';
import styles from './DataEditor.module.css';

/** The rows of one page the editor loads ("Load more rows" appends the next, G-S2 6A). */
export const EDITOR_ROWS = 100;
/** At most this many rows are loaded for editing (the grid has an editor per cell). */
export const MAX_EDITOR_ROWS = 1000;
const MAX_BYTES_KIB = MAX_BYTES_VALUE / 1024;

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

/**
 * Unlock the write mode of a connection with one of its write profiles, its name typed (D-03). Shared with the
 * Schema Designer's database creation (STU-CR-3), which passes its own `lead`.
 */
export function UnlockForm({
  fetcher,
  connectionId,
  profileId,
  onUnlocked,
  lead,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  profileId: string;
  onUnlocked: () => void;
  lead?: string;
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
      <p className={connectionStyles.hint}>{lead ?? t('dataEditor.unlock.lead')}</p>
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
  | { status: 'ready'; table: EditorTable; loaded: LoadedRows };

/** The changes under review, with the row each one comes from (to place the run's refusal, G-S2 6A). */
type Stage =
  | { step: 'edit' }
  | { step: 'preview'; preview: PlanPreview; changes: Change[]; origins: CellRef['row'][] }
  | { step: 'confirmed'; preview: PlanPreview; changes: Change[]; origins: CellRef['row'][] };

function sameRow(first: CellRef['row'], second: CellRef['row']): boolean {
  return 'loaded' in first
    ? 'loaded' in second && first.loaded === second.loaded
    : 'added' in second && first.added === second.added;
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
  /**
   * The cells and rows of the server's last refusal (`studio.data_editor_value_invalid` before the plan,
   * `studio.data_editor_constraint` from the run), until the next edit.
   */
  const [refused, setRefused] = useState<CellIssue[]>([]);
  /** A next page is being loaded, or its columns were not those of the loaded rows. */
  const [more, setMore] = useState<'idle' | 'loading' | 'changed'>('idle');
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
      else setLoad({ status: 'ready', table: table.value, loaded: firstRows(page.value) });
    })();
    return () => {
      alive = false;
    };
  }, [fetcher, connectionId, schema, name, round, onLocked]);

  const columns = useMemo(() => (load.status === 'ready' ? load.loaded.columns : []), [load]);
  const { changes, origins, issues } = useMemo(() => {
    if (load.status !== 'ready') return { changes: [], origins: [], issues: [] };
    const resolved = resolveEdits(load.table, columns, load.loaded.rows, edits);
    return {
      ...buildChangeset(load.table.primaryKey, columns, load.loaded.rows, resolved.edits),
      issues: resolved.issues,
    };
  }, [load, columns, edits]);
  const tooLarge = useMemo(
    () =>
      load.status === 'ready' &&
      changes.length > 0 &&
      changesetBytes({
        schema: load.table.schema,
        table: load.table.table,
        columns,
        changes,
      }) > MAX_CHANGESET_BYTES,
    [load, columns, changes],
  );

  const reload = useCallback(() => {
    setRefused([]);
    setMore('idle');
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
  const { table, loaded } = load;
  const { rows, cut } = loaded;
  const keyShown = table.primaryKey.every((key) => columns.includes(key));
  const editing = stage.step === 'edit';

  const allIssues = [...issues, ...refused];
  const issueOf = (row: CellRef['row'], column: string | null): CellIssue['code'] | null =>
    allIssues.find((issue) => issue.column === column && sameRow(issue.row, row))?.code ?? null;
  const issueId = (row: CellRef['row'], column: string | null) =>
    `${id}-issue-${'loaded' in row ? `r${row.loaded}` : `n${row.added}`}-${column === null ? 'row' : columns.indexOf(column)}`;
  const typeOf = (column: string) => table.columnTypes.get(column) ?? untyped(column);
  const rowLabel = (row: CellRef['row']) =>
    'loaded' in row
      ? t('dataEditor.issueRow', { row: row.loaded + 1 })
      : t('dataEditor.issueNewRow', { row: row.added + 1 });
  const issueText = (issue: Pick<CellIssue, 'column' | 'code'>) => {
    if (issue.code.startsWith('constraint.')) {
      return t(`dataEditor.constraint.${issue.code.slice('constraint.'.length)}`);
    }
    const type = typeOf(issue.column ?? '');
    return t(`dataEditor.issue.${issue.code.slice('value.'.length)}`, {
      format: t(`dataEditor.format.${type.array ? 'array' : type.kind}`),
      length: type.length ?? 0,
      max: MAX_BYTES_KIB,
    });
  };

  const edited = () => {
    setSaved(null);
    setRefused([]);
  };
  const setCell = (row: number, column: string, value: unknown) => {
    edited();
    setEdits((current) => {
      const cells = new Map(current.cells);
      const rowCells = new Map(cells.get(row) ?? []);
      rowCells.set(column, value);
      cells.set(row, rowCells);
      return { ...current, cells };
    });
  };
  const setNew = (index: number, column: string, value: unknown) => {
    edited();
    setEdits((current) => ({
      ...current,
      inserted: current.inserted.map((row, at) =>
        at === index ? { ...row, [column]: value } : row,
      ),
    }));
  };
  const unsetNew = (index: number, column: string) => {
    edited();
    setEdits((current) => ({
      ...current,
      inserted: current.inserted.map((row, at) =>
        at === index
          ? Object.fromEntries(Object.entries(row).filter(([name]) => name !== column))
          : row,
      ),
    }));
  };
  const toggleDelete = (row: number) => {
    edited();
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
      setStage({ step: 'preview', preview: result.value, changes, origins });
    } else {
      setProblem(result.problem);
      setRefused(serverIssues(result.problem.issues, origins, table.columns));
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
      setMore('idle');
      setLoad({ status: 'loading' });
      setRound((value) => value + 1);
    } else {
      setProblem(result.problem);
      // G-S2 6A: a rule of the table, marked at the change's cell or row (the rows of the changes as sent).
      setRefused(serverIssues(result.problem.issues, stage.origins, table.columns));
    }
  };
  const loadMore = async () => {
    if (loaded.next === null) return;
    setMore('loading');
    const page = await fetchPage(fetcher, connectionId, schema, name, {
      limit: EDITOR_ROWS,
      sort: null,
      desc: false,
      filters: [],
      after: loaded.next,
    });
    if (!page.ok) {
      setMore('idle');
      setProblem(page.problem);
      return;
    }
    const next = appendRows(loaded, page.value, table.primaryKey);
    setMore(next === null ? 'changed' : 'idle');
    // Appended only to the rows it continues: a reload meanwhile has other rows (and no edits of these).
    if (next !== null) {
      setLoad((current) =>
        current.status === 'ready' && current.loaded === loaded
          ? { ...current, loaded: next }
          : current,
      );
    }
  };
  const lock = async () => {
    await lockWrite(fetcher, connectionId);
    onLocked();
  };

  const cell = (
    row: CellRef['row'],
    column: string,
    value: unknown,
    original: unknown,
    locked: boolean,
    onChange: (value: unknown) => void,
    added?: { unset: boolean; onUnset: () => void },
  ) => {
    const issue = issueOf(row, column);
    const label =
      'loaded' in row
        ? t('dataEditor.cell', {
            row: row.loaded + 1,
            column,
            interpolation: { escapeValue: false },
          })
        : t('dataEditor.newCell', {
            row: row.added + 1,
            column,
            interpolation: { escapeValue: false },
          });
    return (
      <>
        <CellEditor
          column={typeOf(column)}
          label={label}
          value={value}
          original={original}
          unset={added?.unset}
          onUnset={added?.onUnset}
          disabled={locked || !editing}
          issueId={issue !== null ? issueId(row, column) : null}
          onChange={onChange}
        />
        {issue !== null ? (
          <span id={issueId(row, column)} className={styles.issue}>
            {issueText({ column, code: issue })}
          </span>
        ) : null}
      </>
    );
  };

  return (
    <div className={styles.editor} data-testid="data-editor">
      <div className={styles.summary}>
        <span role="status">{t('dataEditor.unlocked')}</span>
        <Button size="sm" onClick={() => void lock()}>
          {t('dataEditor.lock')}
        </Button>
      </div>
      <p className={connectionStyles.hint} data-testid="data-editor-help">
        {t('dataEditor.help')}
      </p>
      {table.triggers.length > 0 || table.rules.length > 0 ? (
        // Owner decision 2026-11-15: a table with triggers or rules gets a notice, not a block.
        <p role="note" className={connectionStyles.warning} data-testid="data-editor-side-effects">
          {t('dataEditor.sideEffects', {
            names: [...table.triggers, ...table.rules].join(', '),
            interpolation: { escapeValue: false },
          })}
        </p>
      ) : null}
      {table.rowSecurity ? (
        <p role="note" className={connectionStyles.warning} data-testid="data-editor-row-security">
          {t('dataEditor.rowSecurity')}
        </p>
      ) : null}
      {!keyShown ? (
        <p role="alert" className={connectionStyles.problem}>
          {t('dataEditor.keyNotShown')}
        </p>
      ) : null}
      <p className={connectionStyles.hint} data-testid="data-editor-loaded">
        {loaded.next === null
          ? t('dataEditor.allRows', { count: rows.length })
          : rows.length >= MAX_EDITOR_ROWS
            ? t('dataEditor.rowLimit', { count: rows.length })
            : t('dataEditor.someRows', { count: rows.length })}
      </p>
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
                  {typeLabel(typeOf(column)) !== '' ? (
                    <span className={styles.typeLabel}>{typeLabel(typeOf(column))}</span>
                  ) : null}
                </th>
              ))}
              <th scope="col">{t('dataEditor.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => {
              const deleted = edits.deleted.has(index);
              const locked = cut.has(index) || !keyShown;
              const rowIssue = issueOf({ loaded: index }, null);
              return (
                <tr
                  key={`row-${index}`}
                  className={clsx(deleted && styles.removed, rowIssue !== null && styles.refused)}
                  data-row={index}
                >
                  {columns.map((column, at) => {
                    const original = row[at] ?? null;
                    const value = edits.cells.get(index)?.has(column)
                      ? edits.cells.get(index)?.get(column)
                      : original;
                    return (
                      <td
                        key={column}
                        className={clsx(!sameValue(value, original) && styles.changed)}
                      >
                        {cell(
                          { loaded: index },
                          column,
                          value,
                          original,
                          locked || deleted,
                          (next) => setCell(index, column, next),
                        )}
                      </td>
                    );
                  })}
                  <td>
                    <Button
                      size="sm"
                      variant={deleted ? 'secondary' : 'dangerOutline'}
                      disabled={locked || !editing}
                      aria-describedby={
                        rowIssue !== null ? issueId({ loaded: index }, null) : undefined
                      }
                      onClick={() => toggleDelete(index)}
                    >
                      {deleted ? t('dataEditor.undoDelete') : t('dataEditor.delete')}
                    </Button>
                    {rowIssue !== null ? (
                      <span id={issueId({ loaded: index }, null)} className={styles.issue}>
                        {issueText({ column: null, code: rowIssue })}
                      </span>
                    ) : null}
                  </td>
                </tr>
              );
            })}
            {edits.inserted.map((row, index) => {
              const rowIssue = issueOf({ added: index }, null);
              return (
                <tr
                  key={`new-${index}`}
                  className={clsx(styles.added, rowIssue !== null && styles.refused)}
                  data-new-row={index}
                >
                  {columns.map((column) => (
                    <td key={column}>
                      {cell(
                        { added: index },
                        column,
                        column in row ? row[column] : null,
                        '',
                        false,
                        (next) => setNew(index, column, next),
                        { unset: !(column in row), onUnset: () => unsetNew(index, column) },
                      )}
                    </td>
                  ))}
                  <td>
                    <Button
                      size="sm"
                      disabled={!editing}
                      aria-describedby={
                        rowIssue !== null ? issueId({ added: index }, null) : undefined
                      }
                      onClick={() => {
                        edited();
                        setEdits((current) => ({
                          ...current,
                          inserted: current.inserted.filter((_, at) => at !== index),
                        }));
                      }}
                    >
                      {t('dataEditor.removeNew')}
                    </Button>
                    {rowIssue !== null ? (
                      <span id={issueId({ added: index }, null)} className={styles.issue}>
                        {issueText({ column: null, code: rowIssue })}
                      </span>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className={styles.summary}>
        {loaded.next !== null && rows.length < MAX_EDITOR_ROWS ? (
          <Button
            disabled={!editing || more === 'loading'}
            onClick={() => void loadMore()}
            data-testid="data-editor-more"
          >
            {t('dataEditor.loadMore', { count: EDITOR_ROWS })}
          </Button>
        ) : null}
        <Button
          disabled={!editing || !keyShown}
          onClick={() => {
            edited();
            setEdits((current) => ({ ...current, inserted: [...current.inserted, {}] }));
          }}
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
          disabled={
            !editing ||
            changes.length === 0 ||
            changes.length > table.maxChanges ||
            issues.length > 0 ||
            tooLarge
          }
          onClick={() => void preview()}
        >
          {t('dataEditor.preview')}
        </Button>
      </div>
      {more === 'changed' ? (
        <div
          role="alert"
          className={connectionStyles.problem}
          data-testid="data-editor-more-changed"
        >
          <span>{t('dataEditor.columnsChanged')}</span>
          <Button size="sm" onClick={reload}>
            {t('dataEditor.reload')}
          </Button>
        </div>
      ) : null}
      {tooLarge && editing ? (
        <p role="alert" className={connectionStyles.problem} data-testid="data-editor-too-large">
          {t('dataEditor.changesetTooLarge', { max: MAX_CHANGESET_BYTES / 1024 })}
        </p>
      ) : null}
      {allIssues.length > 0 && editing ? (
        // Not a live region: it changes while the user types; each cell names its own issue (aria-describedby).
        <div className={connectionStyles.problem} data-testid="data-editor-issues">
          <span>
            {allIssues.every((issue) => issue.code.startsWith('value.'))
              ? t('dataEditor.issuesLead', { count: allIssues.length })
              : t('dataEditor.refusedLead', { count: allIssues.length })}
          </span>
          <ul className={styles.changes}>
            {allIssues.map((issue, index) => (
              <li key={index}>
                {issue.column === null
                  ? t('dataEditor.issueAtRow', {
                      row: rowLabel(issue.row),
                      issue: issueText(issue),
                      interpolation: { escapeValue: false },
                    })
                  : t('dataEditor.issueAt', {
                      row: rowLabel(issue.row),
                      column: issue.column,
                      issue: issueText(issue),
                      interpolation: { escapeValue: false },
                    })}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {changes.length > 0 && editing ? (
        <ChangeOverview
          changes={changes}
          primaryKey={table.primaryKey}
          testId="data-editor-changes"
        />
      ) : null}
      {stage.step !== 'edit' ? (
        <form
          className={connectionStyles.form}
          onSubmit={(event) => void confirm(event)}
          aria-labelledby={`${id}-review`}
          data-testid="data-editor-review"
        >
          <h3 id={`${id}-review`}>{t('dataEditor.review.title')}</h3>
          <ChangeOverview
            changes={stage.changes}
            primaryKey={table.primaryKey}
            testId="data-editor-review-changes"
          />
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
