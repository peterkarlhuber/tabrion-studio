/**
 * The Schema Designer's table view (STU-CR-4; STU-ADR-0007 sections 2 and 3, gate G-S3, not granted). Rendered only
 * while the server says the `schema_designer` flag is on (`GET /api/write/areas`); the server refuses every route
 * while it is off (405) and decides everything that matters.
 *
 * - **Target:** an open connection of one of the user's own profiles that is not PROD, unlocked with its write profile
 *   (typed name, 15 minutes, as in the Data Editor).
 * - **Draft:** started from one schema of that connection and held only by the server. The page sends one edit
 *   operation at a time (add, drop or rename a table or column; change a column; add or drop a key, constraint or
 *   index) on the revision it shows, and shows the draft the server answers. It never sends a model or a plan and
 *   builds no SQL.
 * - **Preview:** the plan tabrion makes from a fresh snapshot of the database to the draft: every step with its risk
 *   classes, whether it runs in a transaction and its SQL, both fingerprints and the plan hash, the warnings of
 *   STU-ADR-0007 section 8 and, when the server does not run it, why.
 * - **Run (STU-CR-5):** the user types the write profile's name and each table's name, ticks every data-changing
 *   step and the lock, and keeps the backup (or, on DEV only, types the file's name to leave it out); the page sends
 *   that with the plan hash the server recorded. Then **Run the plan** or a **Dry run** (only on request; rolled
 *   back). The server plans again, compares, makes the backup and runs; no button has the focus by default.
 *
 * The graphical ER mode (STU-XSD-1) will send the same edits to the same routes.
 */
import type { TFunction } from 'i18next';
import { useCallback, useEffect, useId, useState, type FormEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { fetchOpenConnections, type OpenedConnection } from '../../api/connections';
import { fetchUnlocks } from '../../api/dataEditor';
import type { Problem, ProblemIssue } from '../../api/problem';
import { fetchProfiles, type ProfileItem } from '../../api/profiles';
import {
  discardDraft,
  editDraft,
  confirmPlan,
  previewDraft,
  runPlan,
  writeRevision,
  type RevisionWritten,
  startDraft,
  viewDraft,
  type ConstraintKind,
  type Draft,
  type DraftColumn,
  type DraftEdit,
  type DraftTable,
  type PlanConfirmed,
  type PlanPreview,
  type PlanStep,
  type PlanWarning,
  type RunOutcome,
} from '../../api/schemaDesigner';
import { fetchWriteAreas } from '../../api/write';
import { Button } from '../../components/ui/Button';
import { visible } from '../../lib/visible';
import connectionStyles from '../connections/Connections.module.css';
import { Field } from '../connections/Field';
import { problemText } from '../connections/messages';
import styles from '../Views.module.css';
import { UnlockForm } from './DataEditor';
import designer from './SchemaDesigner.module.css';

export function SchemaDesignerSection({ fetcher = fetch }: { fetcher?: typeof fetch }) {
  const [on, setOn] = useState(false);
  useEffect(() => {
    let alive = true;
    void fetchWriteAreas(fetcher).then((areas) => {
      if (alive) setOn(areas.schema_designer);
    });
    return () => {
      alive = false;
    };
  }, [fetcher]);
  if (!on) return null;
  return <SchemaDesigner fetcher={fetcher} />;
}

/** The open connections of the user's own profiles that are not PROD (the only ones that can unlock). */
function candidates(opened: OpenedConnection[], profiles: ProfileItem[]) {
  return opened.flatMap((connection) => {
    const profile = profiles.find((item) => item.id === connection.profileId);
    return profile !== undefined && profile.source === 'user' && profile.environment !== 'prod'
      ? [{ connection, profile }]
      : [];
  });
}

/** The problems after which the unlock is gone: the page shows the unlock again. */
const LOCKED = new Set(['studio.write_locked', 'studio.write_target_changed']);

function ProblemLine({ problem }: { problem: Problem | null }) {
  const { t } = useTranslation();
  if (problem === null) return null;
  return (
    <p role="alert" className={connectionStyles.error} data-testid="schema-designer-problem">
      {problemText(t, problem)}
    </p>
  );
}

function SchemaDesigner({ fetcher }: { fetcher: typeof fetch }) {
  const { t } = useTranslation();
  const id = useId();
  const [targets, setTargets] = useState<ReturnType<typeof candidates> | null>(null);
  const [chosen, setChosen] = useState('');
  const [unlock, setUnlock] = useState<{
    connectionId: string;
    unlocked: boolean;
    ended?: Problem | null;
  } | null>(null);

  useEffect(() => {
    let alive = true;
    void Promise.all([fetchOpenConnections(fetcher), fetchProfiles(fetcher)]).then(
      ([opened, list]) => {
        if (!alive) return;
        const found = candidates(opened ?? [], list?.profiles ?? []);
        setTargets(found);
        setChosen((current) => current || (found[0]?.connection.connectionId ?? ''));
      },
    );
    return () => {
      alive = false;
    };
  }, [fetcher]);

  useEffect(() => {
    if (chosen === '') return undefined;
    let alive = true;
    void fetchUnlocks(fetcher).then((list) => {
      if (alive) {
        setUnlock({
          connectionId: chosen,
          unlocked: list.some((item) => item.connectionId === chosen),
        });
      }
    });
    return () => {
      alive = false;
    };
  }, [fetcher, chosen]);

  let body: ReactNode;
  if (targets === null) {
    body = <p className={connectionStyles.hint}>{t('schemaDesigner.loading')}</p>;
  } else if (targets.length === 0) {
    body = (
      <p className={connectionStyles.hint} data-testid="schema-designer-none">
        {t('schemaDesigner.noConnection')}
      </p>
    );
  } else {
    const target = targets.find((item) => item.connection.connectionId === chosen) ?? targets[0];
    if (target === undefined) return null;
    const connectionId = target.connection.connectionId;
    const unlocked = unlock?.connectionId === connectionId ? unlock.unlocked : null;
    const ended = unlock?.connectionId === connectionId ? (unlock.ended ?? null) : null;
    const setUnlocked = (value: boolean, why: Problem | null = null) =>
      setUnlock({ connectionId, unlocked: value, ended: why });
    body = (
      <div className={designer.designer}>
        <Field label={t('schemaDesigner.connection')}>
          {(props) => (
            <select
              {...props}
              className={connectionStyles.input}
              value={connectionId}
              onChange={(event) => setChosen(event.target.value)}
            >
              {targets.map((item) => (
                <option key={item.connection.connectionId} value={item.connection.connectionId}>
                  {visible(item.profile.name)}
                </option>
              ))}
            </select>
          )}
        </Field>
        {unlocked === null ? (
          <p className={connectionStyles.hint}>{t('schemaDesigner.loading')}</p>
        ) : unlocked ? (
          <DraftPanel
            key={connectionId}
            fetcher={fetcher}
            connectionId={connectionId}
            onLocked={(why) => setUnlocked(false, why)}
          />
        ) : (
          <>
            <ProblemLine problem={ended} />
            <UnlockForm
              fetcher={fetcher}
              connectionId={connectionId}
              profileId={target.profile.id}
              lead={t('schemaDesigner.unlockLead')}
              onUnlocked={() => setUnlocked(true)}
            />
          </>
        )}
      </div>
    );
  }
  return (
    <section className={styles.card} aria-labelledby={`${id}-title`} data-testid="schema-designer">
      <h2 id={`${id}-title`} className={styles.cardTitle}>
        {t('schemaDesigner.title')}
      </h2>
      <p className={styles.lead}>{t('schemaDesigner.lead')}</p>
      {body}
    </section>
  );
}

/** The action the user opened on a table or column; one form at a time. */
type Action =
  | { op: 'add_table' }
  | {
      op: 'rename_table' | 'drop_table' | 'add_column' | 'add_constraint' | 'add_index';
      table: string;
    }
  | { op: 'rename_column' | 'alter_column'; table: string; column: DraftColumn };

function DraftPanel({
  fetcher,
  connectionId,
  onLocked,
}: {
  fetcher: typeof fetch;
  connectionId: string;
  onLocked: (why: Problem) => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<Draft | null | undefined>(undefined);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [schema, setSchema] = useState('');
  const [busy, setBusy] = useState(false);
  const [action, setAction] = useState<Action | null>(null);
  const [preview, setPreview] = useState<PlanPreview | null>(null);
  const [outcome, setOutcome] = useState<RunOutcome | null>(null);

  const fail = useCallback(
    (found: Problem) => {
      if (LOCKED.has(found.code)) onLocked(found);
      else setProblem(found);
    },
    [onLocked],
  );

  useEffect(() => {
    let alive = true;
    void viewDraft(fetcher, connectionId).then((result) => {
      if (!alive) return;
      if (result.ok) setDraft(result.value);
      else if (result.problem.code === 'studio.schema_draft_missing') setDraft(null);
      else {
        setDraft(null);
        fail(result.problem);
      }
    });
    return () => {
      alive = false;
    };
  }, [fetcher, connectionId, fail]);

  const start = async (event: FormEvent) => {
    event.preventDefault();
    setProblem(null);
    setBusy(true);
    const result = await startDraft(fetcher, connectionId, schema.trim());
    setBusy(false);
    setPreview(null);
    if (result.ok) setDraft(result.value);
    else fail(result.problem);
  };

  const discard = async () => {
    setProblem(null);
    const result = await discardDraft(fetcher, connectionId);
    if (!result.ok) {
      fail(result.problem);
      return;
    }
    setDraft(null);
    setPreview(null);
    setAction(null);
  };

  const reload = async () => {
    const result = await viewDraft(fetcher, connectionId);
    if (result.ok) setDraft(result.value);
    else if (result.problem.code === 'studio.schema_draft_missing') setDraft(null);
    else fail(result.problem);
  };

  /** Send one edit; the answer is the whole draft. A refusal stays with the form that sent it. */
  const edit = async (change: DraftEdit): Promise<Problem | null> => {
    if (draft === null || draft === undefined) return null;
    setProblem(null);
    const result = await editDraft(fetcher, connectionId, draft.revision, change);
    if (result.ok) {
      setDraft(result.value);
      setPreview(null);
      setAction(null);
      return null;
    }
    if (LOCKED.has(result.problem.code)) onLocked(result.problem);
    if (result.problem.code === 'studio.schema_draft_missing') setDraft(null);
    if (result.problem.code === 'studio.schema_draft_stale') void reload();
    return result.problem;
  };

  const showPreview = async () => {
    setProblem(null);
    setOutcome(null);
    setBusy(true);
    const result = await previewDraft(fetcher, connectionId);
    setBusy(false);
    if (result.ok) setPreview(result.value);
    else {
      setPreview(null);
      fail(result.problem);
    }
  };

  /** A run (or a dry run) used the plan up: the preview goes; after a real run the draft is read again. */
  const ran = (result: RunOutcome) => {
    setPreview(null);
    setOutcome(result);
    if (result.dryRun) return;
    if (result.draftEnded) setDraft(null);
    else void reload();
  };

  /** A refused run also used the plan up (or ended the unlock): the preview goes and the problem shows. */
  const runFailed = (found: Problem) => {
    setPreview(null);
    fail(found);
  };

  if (draft === undefined)
    return <p className={connectionStyles.hint}>{t('schemaDesigner.loading')}</p>;
  if (draft === null) {
    return (
      <form
        className={connectionStyles.form}
        onSubmit={(event) => void start(event)}
        data-testid="schema-designer-start"
      >
        <p className={connectionStyles.hint}>{t('schemaDesigner.startLead')}</p>
        <Field label={t('schemaDesigner.schema')} hint={t('schemaDesigner.schemaHint')}>
          {(props) => (
            <input
              {...props}
              className={connectionStyles.input}
              value={schema}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => setSchema(event.target.value)}
            />
          )}
        </Field>
        <ProblemLine problem={problem} />
        {outcome !== null ? <RunResult outcome={outcome} /> : null}
        <div className={connectionStyles.actions}>
          <Button type="submit" variant="primary" disabled={busy}>
            {t('schemaDesigner.start')}
          </Button>
        </div>
      </form>
    );
  }

  return (
    <div className={designer.designer} data-testid="schema-designer-draft">
      <p className={designer.meta}>
        {t('schemaDesigner.draftMeta', {
          schema: visible(draft.schema),
          tables: draft.tables.length,
          edits: draft.edits,
          interpolation: { escapeValue: false },
        })}
      </p>
      <p className={connectionStyles.hint}>{t('schemaDesigner.renameNote')}</p>
      <div className={designer.toolbar}>
        <Button onClick={() => setAction({ op: 'add_table' })}>
          {t('schemaDesigner.addTable')}
        </Button>
        <Button variant="primary" onClick={() => void showPreview()} disabled={busy}>
          {t('schemaDesigner.preview')}
        </Button>
        <Button variant="ghost" onClick={() => void discard()}>
          {t('schemaDesigner.discard')}
        </Button>
      </div>
      <ProblemLine problem={problem} />
      <PlanIssues problem={problem} draft={draft} />
      {outcome !== null ? <RunResult outcome={outcome} /> : null}
      {action?.op === 'add_table' ? (
        <EditForm action={action} draft={draft} onSubmit={edit} onCancel={() => setAction(null)} />
      ) : null}
      {draft.dropped.length > 0 ? (
        <p className={designer.meta} data-testid="schema-designer-dropped">
          {t('schemaDesigner.droppedTables', {
            names: draft.dropped.map(visible).join(', '),
            interpolation: { escapeValue: false },
          })}
        </p>
      ) : null}
      {draft.tables.map((table) => (
        <TableCard
          key={table.name}
          table={table}
          draft={draft}
          action={
            action !== null && 'table' in action && action.table === table.name ? action : null
          }
          onAction={setAction}
          onEdit={edit}
        />
      ))}
      {preview !== null ? (
        <PreviewPanel
          key={preview.planHash}
          preview={preview}
          fetcher={fetcher}
          connectionId={connectionId}
          onRan={ran}
          onRunFailed={runFailed}
          onLocked={onLocked}
        />
      ) : null}
    </div>
  );
}

function TableCard({
  table,
  draft,
  action,
  onAction,
  onEdit,
}: {
  table: DraftTable;
  draft: Draft;
  action: Action | null;
  onAction: (action: Action | null) => void;
  onEdit: (edit: DraftEdit) => Promise<Problem | null>;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [problem, setProblem] = useState<Problem | null>(null);
  const name = visible(table.name);
  const send = async (edit: DraftEdit) => setProblem(await onEdit(edit));
  const statusClass =
    table.status === 'new' ? designer.new : table.status === 'changed' ? designer.changed : '';
  return (
    <section
      className={designer.table}
      aria-labelledby={`${id}-name`}
      data-testid="schema-designer-table"
    >
      <div className={designer.tableHeader}>
        <h3 id={`${id}-name`} className={designer.tableName}>
          {name}
        </h3>
        <span className={`${designer.badge} ${statusClass}`}>
          {t(`schemaDesigner.status.${table.status}`)}
        </span>
        {table.features.length > 0 || table.triggers > 0 || table.policies > 0 ? (
          <span className={designer.badge}>{t('schemaDesigner.locked')}</span>
        ) : null}
      </div>
      <div className={designer.scroll}>
        <table className={designer.grid}>
          <caption className="tb-sr-only">{t('schemaDesigner.columnsOf', { name })}</caption>
          <thead>
            <tr>
              <th scope="col">{t('schemaDesigner.column.name')}</th>
              <th scope="col">{t('schemaDesigner.column.type')}</th>
              <th scope="col">{t('schemaDesigner.column.nullable')}</th>
              <th scope="col">{t('schemaDesigner.column.default')}</th>
              <th scope="col">{t('schemaDesigner.column.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {table.columns.map((column) => (
              <tr key={column.name}>
                <th scope="row" className={designer.code}>
                  {visible(column.name)}
                  {table.primaryKey.includes(column.name) ? ` (${t('schemaDesigner.pk')})` : ''}
                </th>
                <td className={designer.code}>{visible(column.type)}</td>
                <td>{column.nullable ? t('schemaDesigner.yes') : t('schemaDesigner.no')}</td>
                <td className={designer.code}>
                  {column.default === null ? '' : visible(column.default)}
                </td>
                <td>
                  <div className={designer.toolbar}>
                    <Button
                      size="sm"
                      onClick={() => onAction({ op: 'alter_column', table: table.name, column })}
                    >
                      {t('schemaDesigner.change')}
                      <span className="tb-sr-only"> {visible(column.name)}</span>
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => onAction({ op: 'rename_column', table: table.name, column })}
                    >
                      {t('schemaDesigner.rename')}
                      <span className="tb-sr-only"> {visible(column.name)}</span>
                    </Button>
                    <Button
                      size="sm"
                      variant="dangerOutline"
                      onClick={() =>
                        void send({ op: 'drop_column', table: table.name, column: column.name })
                      }
                    >
                      {t('schemaDesigner.drop')}
                      <span className="tb-sr-only"> {visible(column.name)}</span>
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Elements table={table} onDrop={(edit) => void send(edit)} />
      <ProblemLine problem={problem} />
      <div className={designer.toolbar}>
        <Button size="sm" onClick={() => onAction({ op: 'add_column', table: table.name })}>
          {t('schemaDesigner.addColumn')}
        </Button>
        <Button size="sm" onClick={() => onAction({ op: 'add_constraint', table: table.name })}>
          {t('schemaDesigner.addConstraint')}
        </Button>
        <Button size="sm" onClick={() => onAction({ op: 'add_index', table: table.name })}>
          {t('schemaDesigner.addIndex')}
        </Button>
        <Button size="sm" onClick={() => onAction({ op: 'rename_table', table: table.name })}>
          {t('schemaDesigner.renameTable')}
        </Button>
        <Button
          size="sm"
          variant="dangerOutline"
          onClick={() => void send({ op: 'drop_table', table: table.name })}
        >
          {t('schemaDesigner.dropTable')}
          <span className="tb-sr-only"> {name}</span>
        </Button>
      </div>
      {action !== null ? (
        <EditForm action={action} draft={draft} onSubmit={onEdit} onCancel={() => onAction(null)} />
      ) : null}
    </section>
  );
}

function Elements({ table, onDrop }: { table: DraftTable; onDrop: (edit: DraftEdit) => void }) {
  const { t } = useTranslation();
  const items: { key: string; label: string; edit: DraftEdit }[] = [];
  const cols = (names: string[]) => names.map(visible).join(', ');
  if (table.primaryKey.length > 0) {
    items.push({
      key: 'pk',
      label: t('schemaDesigner.element.primaryKey', {
        columns: cols(table.primaryKey),
        interpolation: { escapeValue: false },
      }),
      edit: { op: 'drop_constraint', table: table.name, kind: 'primary_key' },
    });
  }
  table.unique.forEach((unique, position) =>
    items.push({
      key: `u${position}`,
      label: t('schemaDesigner.element.unique', {
        name: unique.name === null ? '' : visible(unique.name),
        columns: cols(unique.columns),
        interpolation: { escapeValue: false },
      }),
      edit: { op: 'drop_constraint', table: table.name, kind: 'unique', position },
    }),
  );
  table.foreignKeys.forEach((key, position) =>
    items.push({
      key: `f${position}`,
      label: t('schemaDesigner.element.foreignKey', {
        name: key.name === null ? '' : visible(key.name),
        columns: cols(key.columns),
        table: visible(key.referredTable),
        referred: cols(key.referredColumns),
        interpolation: { escapeValue: false },
      }),
      edit: { op: 'drop_constraint', table: table.name, kind: 'foreign_key', position },
    }),
  );
  table.checks.forEach((check, position) =>
    items.push({
      key: `c${position}`,
      label: t('schemaDesigner.element.check', {
        name: check.name === null ? '' : visible(check.name),
        expression: visible(check.expression),
        interpolation: { escapeValue: false },
      }),
      edit: { op: 'drop_constraint', table: table.name, kind: 'check', position },
    }),
  );
  table.indexes.forEach((index) =>
    items.push({
      key: `i${index.name}`,
      label: t(
        index.unique ? 'schemaDesigner.element.uniqueIndex' : 'schemaDesigner.element.index',
        {
          name: visible(index.name),
          columns: cols(index.columns),
          interpolation: { escapeValue: false },
        },
      ),
      edit: { op: 'drop_index', table: table.name, name: index.name },
    }),
  );
  if (items.length === 0) return null;
  return (
    <ul className={designer.elements} aria-label={t('schemaDesigner.elements')}>
      {items.map((item) => (
        <li key={item.key} className={designer.element}>
          <span className={designer.code}>{item.label}</span>
          <Button size="sm" variant="dangerOutline" onClick={() => onDrop(item.edit)}>
            {t('schemaDesigner.drop')}
            <span className="tb-sr-only"> {item.label}</span>
          </Button>
        </li>
      ))}
    </ul>
  );
}

/** The issues of `problem` for the fields of one form (`edit.<field>`). */
function issuesFor(problem: Problem | null, ...fields: string[]): ProblemIssue[] {
  return (problem?.issues ?? []).filter((issue) =>
    fields.some((field) => issue.pointer === `edit.${field}`),
  );
}

function names(text: string | undefined): string[] {
  return (text ?? '')
    .split(',')
    .map((part) => part.trim())
    .filter((part) => part !== '');
}

const ACTIONS = ['', 'CASCADE', 'SET NULL', 'SET DEFAULT', 'RESTRICT'] as const;

function EditForm({
  action,
  draft,
  onSubmit,
  onCancel,
}: {
  action: Action;
  draft: Draft;
  onSubmit: (edit: DraftEdit) => Promise<Problem | null>;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const column = 'column' in action ? action.column : null;
  const [values, setValues] = useState<Record<string, string>>(() => ({
    name: '',
    type: column?.type ?? '',
    default: column?.default ?? '',
    columns: '',
    referredTable: draft.tables[0]?.name ?? '',
    referredColumns: '',
    onDelete: '',
    expression: '',
    where: '',
    kind: 'unique',
  }));
  const [flags, setFlags] = useState({
    nullable: column?.nullable ?? true,
    noDefault: column !== null && column.default === null,
    primaryKey: true,
    unique: false,
  });
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: string, value: string) =>
    setValues((current) => ({ ...current, [key]: value }));
  const table = 'table' in action ? action.table : '';
  const kind = values.kind as ConstraintKind;

  const build = (): DraftEdit => {
    switch (action.op) {
      case 'add_table':
        return {
          op: 'add_table',
          table: values.name,
          columns: [{ name: values.columns, type: values.type, nullable: !flags.primaryKey }],
          primary_key: flags.primaryKey ? [values.columns] : [],
        };
      case 'rename_table':
        return { op: 'rename_table', table, new_name: values.name };
      case 'add_column': {
        const added: Record<string, unknown> = {
          name: values.name,
          type: values.type,
          nullable: flags.nullable,
        };
        if (values.default !== '') added.default = values.default;
        return { op: 'add_column', table, column: added };
      }
      case 'rename_column':
        return { op: 'rename_column', table, column: action.column.name, new_name: values.name };
      case 'alter_column': {
        const change: DraftEdit = { op: 'alter_column', table, column: action.column.name };
        if (values.type !== action.column.type) change.type = values.type;
        if (flags.nullable !== action.column.nullable) change.nullable = flags.nullable;
        const wanted = flags.noDefault ? null : values.default;
        if (wanted !== action.column.default) change.default = wanted;
        return change;
      }
      case 'add_constraint': {
        const constraint: Record<string, unknown> = { kind };
        if (kind !== 'primary_key' && values.name !== '') constraint.name = values.name;
        if (kind !== 'check') constraint.columns = names(values.columns);
        if (kind === 'foreign_key') {
          constraint.referred_table = values.referredTable;
          constraint.referred_columns = names(values.referredColumns);
          if (values.onDelete !== '') constraint.on_delete = values.onDelete;
        }
        if (kind === 'check') constraint.expression = values.expression;
        return { op: 'add_constraint', table, constraint };
      }
      case 'add_index': {
        const index: Record<string, unknown> = {
          name: values.name,
          columns: names(values.columns),
          unique: flags.unique,
        };
        if (values.where !== '') index.where = values.where;
        return { op: 'add_index', table, index };
      }
      default:
        return { op: action.op, table };
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setProblem(await onSubmit(build()));
    setBusy(false);
  };

  const text = (key: string, label: string, fields: string[], hint?: string, code = true) => (
    <Field label={label} hint={hint} issues={issuesFor(problem, ...fields)}>
      {(props) => (
        <input
          {...props}
          className={`${connectionStyles.input}${code ? ` ${designer.code}` : ''}`}
          value={values[key] ?? ''}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => set(key, event.target.value)}
        />
      )}
    </Field>
  );
  const check = (key: keyof typeof flags, label: string) => (
    <label className={connectionStyles.check}>
      <input
        type="checkbox"
        checked={flags[key]}
        onChange={(event) => setFlags((current) => ({ ...current, [key]: event.target.checked }))}
      />{' '}
      {label}
    </label>
  );

  let fields: ReactNode;
  switch (action.op) {
    case 'add_table':
      fields = (
        <>
          {text('name', t('schemaDesigner.form.tableName'), ['table'])}
          {text('columns', t('schemaDesigner.form.firstColumn'), ['column_name', 'columns'])}
          {text(
            'type',
            t('schemaDesigner.form.type'),
            ['column_type'],
            t('schemaDesigner.form.typeHint'),
          )}
          {check('primaryKey', t('schemaDesigner.form.primaryKey'))}
        </>
      );
      break;
    case 'rename_table':
    case 'rename_column':
      fields = text(
        'name',
        t('schemaDesigner.form.newName'),
        ['new_name'],
        t('schemaDesigner.renameNote'),
      );
      break;
    case 'add_column':
      fields = (
        <>
          {text('name', t('schemaDesigner.form.columnName'), ['column_name'])}
          {text(
            'type',
            t('schemaDesigner.form.type'),
            ['column_type'],
            t('schemaDesigner.form.typeHint'),
          )}
          {check('nullable', t('schemaDesigner.form.nullable'))}
          {text(
            'default',
            t('schemaDesigner.form.default'),
            ['column_default'],
            t('schemaDesigner.form.defaultHint'),
          )}
        </>
      );
      break;
    case 'alter_column':
      fields = (
        <>
          {text('type', t('schemaDesigner.form.type'), ['type'], t('schemaDesigner.form.typeHint'))}
          {check('nullable', t('schemaDesigner.form.nullable'))}
          {check('noDefault', t('schemaDesigner.form.noDefault'))}
          {flags.noDefault
            ? null
            : text(
                'default',
                t('schemaDesigner.form.default'),
                ['default'],
                t('schemaDesigner.form.defaultHint'),
              )}
        </>
      );
      break;
    case 'add_constraint':
      fields = (
        <>
          <Field
            label={t('schemaDesigner.form.kind')}
            issues={issuesFor(problem, 'constraint_kind')}
          >
            {(props) => (
              <select
                {...props}
                className={connectionStyles.input}
                value={kind}
                onChange={(event) => set('kind', event.target.value)}
              >
                {(['primary_key', 'unique', 'foreign_key', 'check'] as const).map((value) => (
                  <option key={value} value={value}>
                    {t(`schemaDesigner.kinds.${value}`)}
                  </option>
                ))}
              </select>
            )}
          </Field>
          {kind !== 'primary_key'
            ? text(
                'name',
                t('schemaDesigner.form.constraintName'),
                ['constraint_name'],
                draft.engine === 'postgresql'
                  ? t('schemaDesigner.form.nameRequired')
                  : t('schemaDesigner.form.nameOptional'),
              )
            : null}
          {kind !== 'check'
            ? text(
                'columns',
                t('schemaDesigner.form.columns'),
                ['constraint_columns'],
                t('schemaDesigner.form.columnsHint'),
              )
            : null}
          {kind === 'foreign_key' ? (
            <>
              <Field
                label={t('schemaDesigner.form.referredTable')}
                issues={issuesFor(problem, 'referred_table')}
              >
                {(props) => (
                  <select
                    {...props}
                    className={connectionStyles.input}
                    value={values.referredTable}
                    onChange={(event) => set('referredTable', event.target.value)}
                  >
                    {draft.tables.map((item) => (
                      <option key={item.name} value={item.name}>
                        {visible(item.name)}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
              {text(
                'referredColumns',
                t('schemaDesigner.form.referredColumns'),
                ['referred_columns'],
                t('schemaDesigner.form.columnsHint'),
              )}
              <Field
                label={t('schemaDesigner.form.onDelete')}
                issues={issuesFor(problem, 'on_delete')}
              >
                {(props) => (
                  <select
                    {...props}
                    className={connectionStyles.input}
                    value={values.onDelete}
                    onChange={(event) => set('onDelete', event.target.value)}
                  >
                    {ACTIONS.map((value) => (
                      <option key={value} value={value}>
                        {value === '' ? t('schemaDesigner.form.noAction') : value}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
            </>
          ) : null}
          {kind === 'check'
            ? text(
                'expression',
                t('schemaDesigner.form.expression'),
                ['expression'],
                t('schemaDesigner.form.expressionHint'),
              )
            : null}
        </>
      );
      break;
    case 'add_index':
      fields = (
        <>
          {text('name', t('schemaDesigner.form.indexName'), ['index_name'])}
          {text(
            'columns',
            t('schemaDesigner.form.columns'),
            ['index_columns'],
            t('schemaDesigner.form.columnsHint'),
          )}
          {check('unique', t('schemaDesigner.form.uniqueIndex'))}
          {text(
            'where',
            t('schemaDesigner.form.where'),
            ['index_where'],
            t('schemaDesigner.form.whereHint'),
          )}
        </>
      );
      break;
    default:
      fields = null;
  }

  const issues = problem?.issues ?? [];
  const shown = issues.filter((issue) => issue.pointer.startsWith('edit.'));
  return (
    <form
      className={connectionStyles.form}
      onSubmit={(event) => void submit(event)}
      aria-labelledby={`${id}-title`}
      data-testid="schema-designer-form"
    >
      <h4 id={`${id}-title`}>
        {t(`schemaDesigner.forms.${action.op}`, {
          table: visible(table),
          column: column === null ? '' : visible(column.name),
          interpolation: { escapeValue: false },
        })}
      </h4>
      {fields}
      {/* A refusal without a field of this form (or of tabrion) is shown as a whole. */}
      <ProblemLine problem={problem !== null && shown.length === 0 ? problem : null} />
      <PlanIssues problem={problem} />
      <div className={connectionStyles.actions}>
        <Button type="submit" variant="primary" disabled={busy}>
          {t('schemaDesigner.apply')}
        </Button>
        <Button onClick={onCancel}>{t('schemaDesigner.cancel')}</Button>
      </div>
    </form>
  );
}

const TABLE_POINTER = /^(tables|dropped)\.(\d+)$/;

/**
 * Tabrion's reason, feature kind or code of a refused change (`plan.*`), why a plan is not run (`plan.reason`) or
 * which check failed (`plan.check`), each with its own text. With the draft, the table the refusal is about: the
 * problem names it only by its position (`tables.<n>` or `dropped.<n>`, GS3-12), and the page looks it up.
 */
export function PlanIssues({ problem, draft }: { problem: Problem | null; draft?: Draft }) {
  const { t } = useTranslation();
  const all = problem?.issues ?? [];
  const issues = all.filter((issue) => issue.pointer.startsWith('plan.'));
  const tables =
    draft === undefined
      ? []
      : all.flatMap((issue) => {
          const match = TABLE_POINTER.exec(issue.pointer);
          if (match === null) return [];
          const position = Number(match[2]);
          const name =
            match[1] === 'tables' ? draft.tables[position]?.name : draft.dropped[position];
          return [name ?? null];
        });
  if (issues.length === 0 && tables.length === 0) return null;
  return (
    <ul className={connectionStyles.issues} data-testid="schema-designer-plan-issues">
      {issues.map((issue) => (
        <li key={`${issue.pointer}:${issue.code}`}>{planIssueText(t, issue)}</li>
      ))}
      {tables.map((name, position) => (
        <li key={`table:${position}`} data-testid="schema-designer-plan-table">
          {name === null
            ? t('schemaDesigner.planIssues.tableUnknown')
            : t('schemaDesigner.planIssues.table', {
                name: visible(name),
                interpolation: { escapeValue: false },
              })}
        </li>
      ))}
    </ul>
  );
}

/** Why the server does not run a plan (`plan.reason`) and which check before the commit failed (`plan.check`). */
const RUN_REASONS = new Set([
  'nothing_to_run',
  'environment',
  'migration_managed',
  'levuvo_managed',
  'alembic_project',
  'non_transactional',
  'too_many_tables',
  'backup_unavailable',
  'foreign_key_check',
  'integrity_check',
  'failed',
]);

function reasonText(t: TFunction, reason: string): string {
  return RUN_REASONS.has(reason)
    ? t(`schemaDesigner.reasons.${reason}`)
    : t('schemaDesigner.planIssues.other', {
        code: reason,
        interpolation: { escapeValue: false },
      });
}

const PLAN_CODES = new Set([
  'plan.unsupported_feature',
  'plan.dependent_object',
  'plan.unsafe_schema_sql',
  'plan.unsupported_change',
  'plan.invalid_target',
  'ddl.unsupported_type',
  'ddl.unsafe_sql_text',
  'ddl.identifier_too_long',
  'ddl.unsupported_feature',
  'ddl.invalid_shape',
]);

function planIssueText(t: TFunction, issue: ProblemIssue): string {
  const reason = issue.code.slice('plan.'.length);
  if (
    (issue.pointer === 'plan.reason' || issue.pointer === 'plan.check') &&
    RUN_REASONS.has(reason)
  ) {
    return reasonText(t, reason);
  }
  if (issue.pointer === 'plan.feature') {
    return t('schemaDesigner.planIssues.feature', {
      feature: issue.code.slice('plan.'.length),
      interpolation: { escapeValue: false },
    });
  }
  return PLAN_CODES.has(issue.code)
    ? t(`schemaDesigner.planIssues.${issue.code.replace('.', '_')}`)
    : t('schemaDesigner.planIssues.other', {
        code: issue.code,
        interpolation: { escapeValue: false },
      });
}

/** A statement as text: line breaks and tabs kept, every other invisible character shown (names in it may be odd). */
function sqlText(sql: string): string {
  return sql
    .split('\n')
    .map((line) => visible(line.replace(/\t/g, '    ')))
    .join('\n');
}

function PreviewPanel({
  preview,
  fetcher,
  connectionId,
  onRan,
  onRunFailed,
  onLocked,
}: {
  preview: PlanPreview;
  fetcher: typeof fetch;
  connectionId: string;
  onRan: (outcome: RunOutcome) => void;
  onRunFailed: (problem: Problem) => void;
  onLocked: (why: Problem) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <section
      className={designer.table}
      aria-labelledby={`${id}-title`}
      data-testid="schema-designer-preview"
    >
      <h3 id={`${id}-title`} className={designer.tableName}>
        {t('schemaDesigner.previewTitle')}
      </h3>
      <p className={connectionStyles.status} role="status">
        {preview.steps.length === 0
          ? t('schemaDesigner.previewEmpty')
          : t('schemaDesigner.previewSteps', { count: preview.steps.length })}{' '}
        {t('schemaDesigner.previewNotRun')}
      </p>
      {preview.baseChanged ? (
        <p className={connectionStyles.warning} data-testid="schema-designer-base-changed">
          {t('schemaDesigner.baseChanged')}
        </p>
      ) : null}
      <dl className={connectionStyles.details}>
        <dt>{t('schemaDesigner.planHash')}</dt>
        <dd className={designer.hash} data-testid="schema-designer-plan-hash">
          {preview.planHash}
        </dd>
        <dt>{t('schemaDesigner.sourceFingerprint')}</dt>
        <dd className={designer.hash}>{preview.sourceFingerprint}</dd>
        <dt>{t('schemaDesigner.targetFingerprint')}</dt>
        <dd className={designer.hash}>{preview.targetFingerprint}</dd>
        <dt>{t('schemaDesigner.serverVersion')}</dt>
        <dd>{preview.serverVersion.join('.')}</dd>
        <dt>{t('schemaDesigner.transactional')}</dt>
        <dd>{preview.transactional ? t('schemaDesigner.yes') : t('schemaDesigner.no')}</dd>
      </dl>
      <PlanSteps steps={preview.steps} />
      <Warnings warnings={preview.warnings} lockTimeoutS={preview.confirmation.lockTimeoutS} />
      {preview.executable ? (
        <RunForm
          preview={preview}
          fetcher={fetcher}
          connectionId={connectionId}
          onRan={onRan}
          onRunFailed={onRunFailed}
          onLocked={onLocked}
        />
      ) : (
        <div className={connectionStyles.warning} data-testid="schema-designer-blocked">
          <p>
            <strong>{t('schemaDesigner.blockedTitle')}</strong>
          </p>
          <ul className={connectionStyles.issues}>
            {preview.blocked.map((reason) => (
              <li key={reason}>{reasonText(t, reason)}</li>
            ))}
          </ul>
          <p>{t('schemaDesigner.blockedLead')}</p>
        </div>
      )}
      <RevisionForm
        preview={preview}
        fetcher={fetcher}
        connectionId={connectionId}
        onLocked={onLocked}
      />
    </section>
  );
}

/** Why the server writes no revision for the plan (STU-XSD-3, `plan.reason`). */
const REVISION_REASONS = new Set([
  'nothing_to_run',
  'environment',
  'project_untrusted',
  'code_untrusted',
  'no_alembic_project',
]);

function revisionReasonText(t: TFunction, reason: string): string {
  return REVISION_REASONS.has(reason)
    ? t(`schemaDesigner.revision.reasons.${reason}`)
    : t('schemaDesigner.planIssues.other', {
        code: reason,
        interpolation: { escapeValue: false },
      });
}

/** The previewed plan written as a new Alembic revision file into the trusted project (STU-XSD-3): the server plans
 * again, reads the single head and creates the file; nothing runs on the database, and the revision never runs. */
function RevisionForm({
  preview,
  fetcher,
  connectionId,
  onLocked,
}: {
  preview: PlanPreview;
  fetcher: typeof fetch;
  connectionId: string;
  onLocked: (why: Problem) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const output = preview.revisionOutput;
  const [message, setMessage] = useState('');
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);
  const [written, setWritten] = useState<RevisionWritten | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setProblem(null);
    setBusy(true);
    const result = await writeRevision(fetcher, connectionId, preview.planHash, message);
    setBusy(false);
    if (result.ok) setWritten(result.value);
    else if (LOCKED.has(result.problem.code)) onLocked(result.problem);
    else setProblem(result.problem);
  };

  const reasons = (problem?.issues ?? [])
    .filter((issue) => issue.pointer === 'plan.reason')
    .map((issue) => issue.code.slice('plan.'.length));

  return (
    <form
      className={connectionStyles.form}
      onSubmit={(event) => void submit(event)}
      aria-labelledby={`${id}-title`}
      data-testid="schema-designer-revision"
    >
      <h4 id={`${id}-title`} className={designer.tableName}>
        {t('schemaDesigner.revision.title')}
      </h4>
      <p className={connectionStyles.hint}>{t('schemaDesigner.revision.lead')}</p>
      <p className={connectionStyles.warning} data-testid="schema-designer-revision-source">
        {t('schemaDesigner.revision.sourceNotChecked', {
          fingerprint: preview.sourceFingerprint,
          interpolation: { escapeValue: false },
        })}
      </p>
      {!output.available ? (
        <div className={connectionStyles.warning} data-testid="schema-designer-revision-blocked">
          <p>
            <strong>{t('schemaDesigner.revision.blockedTitle')}</strong>
          </p>
          <ul className={connectionStyles.issues}>
            {output.blocked.map((reason) => (
              <li key={reason}>{revisionReasonText(t, reason)}</li>
            ))}
          </ul>
        </div>
      ) : written === null ? (
        <>
          <fieldset className={designer.confirm} disabled={busy}>
            <Field label={t('schemaDesigner.revision.message', { count: output.maxMessage })}>
              {(props) => (
                <input
                  {...props}
                  className={connectionStyles.input}
                  value={message}
                  maxLength={output.maxMessage}
                  autoComplete="off"
                  onChange={(event) => setMessage(event.target.value)}
                  data-testid="schema-designer-revision-message"
                />
              )}
            </Field>
          </fieldset>
          <ProblemLine problem={problem} />
          {reasons.length > 0 ? (
            <ul className={connectionStyles.issues}>
              {reasons.map((reason) => (
                <li key={reason}>{revisionReasonText(t, reason)}</li>
              ))}
            </ul>
          ) : null}
          <div className={connectionStyles.actions}>
            <Button
              type="submit"
              variant="primary"
              disabled={busy || message.trim() === ''}
              data-testid="schema-designer-revision-write"
            >
              {t('schemaDesigner.revision.write')}
            </Button>
          </div>
        </>
      ) : (
        <div
          className={connectionStyles.status}
          role="status"
          data-testid="schema-designer-revision-written"
        >
          <p>
            {t('schemaDesigner.revision.written', {
              path: visible(written.path),
              revision: written.revision,
              down: visible(written.downRevision),
              interpolation: { escapeValue: false },
            })}
          </p>
          {written.codeTrustChanged ? <p>{t('schemaDesigner.revision.trustAgain')}</p> : null}
          {written.auditPending ? <p>{t('schemaDesigner.run.auditPending')}</p> : null}
        </div>
      )}
    </form>
  );
}

/** The steps of a plan with their risk classes, transactional flag and exact SQL (STU-CR-4, STU-XSD-2). */
export function PlanSteps({ steps }: { steps: PlanStep[] }) {
  const { t } = useTranslation();
  if (steps.length === 0) return null;
  return (
    <div className={designer.scroll}>
      <table className={designer.grid}>
        <caption className="tb-sr-only">{t('schemaDesigner.previewTitle')}</caption>
        <thead>
          <tr>
            <th scope="col">{t('schemaDesigner.step.number')}</th>
            <th scope="col">{t('schemaDesigner.step.operation')}</th>
            <th scope="col">{t('schemaDesigner.step.risks')}</th>
            <th scope="col">{t('schemaDesigner.step.transactional')}</th>
            <th scope="col">{t('schemaDesigner.step.sql')}</th>
          </tr>
        </thead>
        <tbody>
          {steps.map((step) => (
            <tr key={step.index} data-testid="schema-designer-step">
              <td>{step.index + 1}</td>
              <th scope="row">
                {t(`schemaDesigner.operations.${step.operation}`, {
                  defaultValue: step.operation,
                })}{' '}
                <span className={designer.code}>{visible(step.target)}</span>
              </th>
              <td>
                <span className={designer.risks}>
                  {step.risks.map((risk) => (
                    <span key={risk} className={designer.badge}>
                      {t(`schemaDesigner.risks.${risk}`, { defaultValue: risk })}
                    </span>
                  ))}
                </span>
              </td>
              <td>{step.transactional ? t('schemaDesigner.yes') : t('schemaDesigner.no')}</td>
              <td>
                {step.statements.map((statement, position) => (
                  <pre key={position} className={designer.sql}>
                    {sqlText(statement.sql)}
                  </pre>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const WARNINGS = new Set([
  'not_modelled',
  'pg_dependent_code',
  'function_drift',
  'sqlite_rowid',
  'fingerprint_gaps',
  'locking',
]);

/** The fixed warnings of the preview (STU-ADR-0007 section 8), each with its own text. */
export function Warnings({
  warnings,
  lockTimeoutS,
}: {
  warnings: PlanWarning[];
  lockTimeoutS: number;
}) {
  const { t } = useTranslation();
  const shown = warnings.filter((warning) => WARNINGS.has(warning.code));
  if (shown.length === 0) return null;
  return (
    <div data-testid="schema-designer-warnings">
      <h4 className={designer.tableName}>{t('schemaDesigner.warningsTitle')}</h4>
      <ul className={connectionStyles.issues}>
        {shown.map((warning, position) => (
          <li key={position} data-testid="schema-designer-warning" data-code={warning.code}>
            {t(`schemaDesigner.warnings.${warning.code}`, {
              table: visible(warning.table ?? ''),
              seconds: warning.lockTimeoutS ?? lockTimeoutS,
              interpolation: { escapeValue: false },
            })}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * The confirmation per risk class (STU-ADR-0007 sections 6 and 7), then the run or a dry run. Nothing here is
 * checked by the page: the server compares every part with the plan it keeps and refuses a mismatch.
 */
function RunForm({
  preview,
  fetcher,
  connectionId,
  onRan,
  onRunFailed,
  onLocked,
}: {
  preview: PlanPreview;
  fetcher: typeof fetch;
  connectionId: string;
  onRan: (outcome: RunOutcome) => void;
  onRunFailed: (problem: Problem) => void;
  onLocked: (why: Problem) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const need = preview.confirmation;
  const [typed, setTyped] = useState('');
  const [tables, setTables] = useState<string[]>(() => need.tables.map(() => ''));
  const [steps, setSteps] = useState<number[]>([]);
  const [locking, setLocking] = useState(false);
  const [skip, setSkip] = useState(false);
  const [skipTyped, setSkipTyped] = useState('');
  const [confirmed, setConfirmed] = useState<PlanConfirmed | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);
  // Review I-2 of PR #137: a run with a backup can take long (pg_dump of the whole database); the page says so.
  const [running, setRunning] = useState<'run' | 'dry' | null>(null);

  const confirm = async (event: FormEvent) => {
    event.preventDefault();
    setProblem(null);
    setBusy(true);
    const result = await confirmPlan(fetcher, connectionId, preview.planHash, {
      typed,
      tables,
      steps,
      locking,
      backup: skip ? 'skip' : 'make',
      backupTyped: skipTyped,
    });
    setBusy(false);
    if (result.ok) setConfirmed(result.value);
    else if (LOCKED.has(result.problem.code)) onLocked(result.problem);
    else setProblem(result.problem);
  };

  const run = async (dryRun: boolean) => {
    setProblem(null);
    setBusy(true);
    setRunning(dryRun ? 'dry' : 'run');
    const result = await runPlan(fetcher, connectionId, preview.planHash, dryRun);
    setRunning(null);
    setBusy(false);
    if (result.ok) onRan(result.value);
    else if (LOCKED.has(result.problem.code)) onLocked(result.problem);
    else onRunFailed(result.problem);
  };

  const toggleStep = (index: number, on: boolean) =>
    setSteps((current) =>
      on
        ? [...current.filter((item) => item !== index), index]
        : current.filter((item) => item !== index),
    );

  return (
    <form
      className={connectionStyles.form}
      onSubmit={(event) => void confirm(event)}
      aria-labelledby={`${id}-title`}
      aria-busy={running !== null}
      data-testid="schema-designer-run"
    >
      <h4 id={`${id}-title`} className={designer.tableName}>
        {t('schemaDesigner.run.title')}
      </h4>
      <p className={connectionStyles.hint}>{t('schemaDesigner.run.lead')}</p>
      <fieldset className={designer.confirm} disabled={confirmed !== null || busy}>
        <Field
          label={t('schemaDesigner.run.typedProfile', {
            name: visible(need.profile),
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
              data-testid="schema-designer-run-profile"
            />
          )}
        </Field>
        {need.tables.map((name, position) => (
          <Field
            key={name}
            label={t('schemaDesigner.run.typedTable', {
              name: visible(name),
              interpolation: { escapeValue: false },
            })}
          >
            {(props) => (
              <input
                {...props}
                className={connectionStyles.input}
                value={tables[position] ?? ''}
                autoComplete="off"
                spellCheck={false}
                onChange={(event) =>
                  setTables((current) =>
                    current.map((item, at) => (at === position ? event.target.value : item)),
                  )
                }
                data-testid="schema-designer-run-table"
              />
            )}
          </Field>
        ))}
        {need.dataChangingSteps.map((index) => (
          <label key={index} className={connectionStyles.check}>
            <input
              type="checkbox"
              checked={steps.includes(index)}
              onChange={(event) => toggleStep(index, event.target.checked)}
              data-testid="schema-designer-run-step"
            />
            {t('schemaDesigner.run.acceptStep', { number: index + 1 })}
          </label>
        ))}
        {need.locking ? (
          <label className={connectionStyles.check}>
            <input
              type="checkbox"
              checked={locking}
              onChange={(event) => setLocking(event.target.checked)}
              data-testid="schema-designer-run-locking"
            />
            {t('schemaDesigner.run.acceptLocking', { seconds: need.lockTimeoutS })}
          </label>
        ) : null}
        <p className={connectionStyles.hint} data-testid="schema-designer-run-backup">
          {!need.backup.required
            ? t('schemaDesigner.run.backupNotNeeded')
            : need.backup.engine === 'postgresql'
              ? t('schemaDesigner.run.backupMadePostgres')
              : t('schemaDesigner.run.backupMade')}
        </p>
        {need.backup.required && need.backup.optOutAllowed && need.backup.optOutText !== null ? (
          <>
            <label className={connectionStyles.check}>
              <input
                type="checkbox"
                checked={skip}
                onChange={(event) => setSkip(event.target.checked)}
                data-testid="schema-designer-run-skip"
              />
              {t('schemaDesigner.run.backupSkip')}
            </label>
            {skip ? (
              <Field
                label={t('schemaDesigner.run.backupSkipTyped', {
                  name: visible(need.backup.optOutText),
                  interpolation: { escapeValue: false },
                })}
              >
                {(props) => (
                  <input
                    {...props}
                    className={connectionStyles.input}
                    value={skipTyped}
                    autoComplete="off"
                    spellCheck={false}
                    onChange={(event) => setSkipTyped(event.target.value)}
                    data-testid="schema-designer-run-skip-typed"
                  />
                )}
              </Field>
            ) : null}
          </>
        ) : null}
      </fieldset>
      <ProblemLine problem={problem} />
      <PlanIssues problem={problem} />
      {confirmed === null ? (
        <div className={connectionStyles.actions}>
          <Button type="submit" variant="primary" disabled={busy}>
            {t('schemaDesigner.run.confirm')}
          </Button>
        </div>
      ) : (
        <>
          <p
            className={connectionStyles.status}
            role="status"
            data-testid="schema-designer-confirmed"
          >
            {t('schemaDesigner.run.confirmed', { seconds: confirmed.expiresInS })}
          </p>
          <p className={connectionStyles.hint}>{t('schemaDesigner.run.dryRunNote')}</p>
          <div className={connectionStyles.actions}>
            <Button
              variant="danger"
              onClick={() => void run(false)}
              disabled={busy}
              data-testid="schema-designer-run-go"
            >
              {t('schemaDesigner.run.run')}
            </Button>
            <Button
              onClick={() => void run(true)}
              disabled={busy}
              data-testid="schema-designer-run-dry"
            >
              {t('schemaDesigner.run.dryRun')}
            </Button>
          </div>
          {running !== null ? (
            <p
              className={connectionStyles.status}
              role="status"
              data-testid="schema-designer-running"
            >
              {running === 'run' && need.backup.required
                ? need.backup.engine === 'postgresql'
                  ? t('schemaDesigner.run.backupRunningPostgres')
                  : t('schemaDesigner.run.backupRunning')
                : t('schemaDesigner.run.running')}
            </p>
          ) : null}
        </>
      )}
    </form>
  );
}

/** What a run did: committed or rolled back (a dry run), its steps and the backup. */
function RunResult({ outcome }: { outcome: RunOutcome }) {
  const { t } = useTranslation();
  return (
    <div className={connectionStyles.status} role="status" data-testid="schema-designer-outcome">
      <p>
        {outcome.result === 'committed'
          ? t('schemaDesigner.run.committed', { count: outcome.stepsRun })
          : t('schemaDesigner.run.rolledBack', { count: outcome.stepsRun })}
      </p>
      {outcome.backupPath !== null ? (
        <p data-testid="schema-designer-backup-path">
          {t('schemaDesigner.run.backupPath', {
            path: visible(outcome.backupPath),
            interpolation: { escapeValue: false },
          })}
        </p>
      ) : null}
      {outcome.backup === 'opted_out' ? <p>{t('schemaDesigner.run.backupSkipped')}</p> : null}
      {outcome.auditPending ? <p>{t('schemaDesigner.run.auditPending')}</p> : null}
      {outcome.draftEnded ? <p>{t('schemaDesigner.run.draftEnded')}</p> : null}
    </div>
  );
}
