/**
 * The Data Editor (STU-026; 03 sections 2 D and 5.2, 08 section 4.6). Behind the Data Editor flag, which the owner of
 * the Studio process sets in `write-areas.toml` and which is off by default (gate G-S2 is not granted). The page
 * never decides the flag: it reads `GET /api/write/areas` to show the editor at all, and the server answers 405 to
 * every route here while the flag is off.
 *
 * - `GET`, `POST`, `DELETE /api/write/unlocks`: this session's unlocks; unlock with the typed write profile name (D-03).
 * - `POST /api/write/data-editor/table`: the table's columns with their types (STU-XDE-1, `cellValues.ts`), primary
 *   key, triggers and rules (the server refuses a view, a partitioned table and a table without a primary key).
 * - `POST /api/write/plans` with the executor `data_editor.changeset`: the changeset as a plan, answered with its
 *   preview; `.../confirm` with the typed text; `.../execute` runs it once in one transaction.
 *
 * A changeset holds cell values. It lives in this page's memory only: it is never stored in the browser, the query
 * history or the drafts (review N-3 of PR #109). The server's answers never echo a value.
 */
import {
  checkValue,
  fieldText,
  parseColumnType,
  resolveText,
  Typed,
  untyped,
  type ColumnType,
  type IssueCode,
} from './cellValues';
import type { ProblemIssue } from './problem';
import { getJson, send, type Result } from './profiles';

export const EXECUTOR = 'data_editor.changeset';

type Doc = Record<string, unknown>;

function doc(value: unknown): Doc | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Doc)
    : null;
}

function strings(value: unknown): string[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
    ? (value as string[])
    : null;
}

// --- unlocks ------------------------------------------------------------------------------------------------------

export interface WriteUnlock {
  connectionId: string;
  writeProfileId: string;
  environment: string;
  expiresInS: number;
}

export function parseUnlocks(body: unknown): WriteUnlock[] {
  const list = doc(body)?.unlocks;
  if (!Array.isArray(list)) return [];
  return list.flatMap((item: unknown) => {
    const entry = doc(item);
    if (
      entry === null ||
      typeof entry.connection_id !== 'string' ||
      typeof entry.write_profile_id !== 'string'
    ) {
      return [];
    }
    return [
      {
        connectionId: entry.connection_id,
        writeProfileId: entry.write_profile_id,
        environment: typeof entry.environment === 'string' ? entry.environment : '',
        expiresInS: typeof entry.expires_in_s === 'number' ? entry.expires_in_s : 0,
      },
    ];
  });
}

export async function fetchUnlocks(fetcher: typeof fetch): Promise<WriteUnlock[]> {
  return parseUnlocks(await getJson(fetcher, '/api/write/unlocks'));
}

/** D-03: unlock the write mode of this connection by typing the write profile's name; the server checks it. */
export function unlockWrite(
  fetcher: typeof fetch,
  connectionId: string,
  writeProfileId: string,
  typedName: string,
): Promise<Result<unknown>> {
  return send(fetcher, 'POST', '/api/write/unlocks', {
    connection_id: connectionId,
    write_profile_id: writeProfileId,
    typed_name: typedName,
  });
}

export function lockWrite(fetcher: typeof fetch, connectionId: string): Promise<Result<null>> {
  return send(fetcher, 'DELETE', '/api/write/unlocks', { connection_id: connectionId });
}

// --- the table ----------------------------------------------------------------------------------------------------

export interface EditorTable {
  schema: string;
  table: string;
  columns: string[];
  /** Each column's type by name (STU-XDE-1); a column the server did not describe is untyped. */
  columnTypes: Map<string, ColumnType>;
  primaryKey: string[];
  triggers: string[];
  rules: string[];
  rowSecurity: boolean;
  maxChanges: number;
}

/** The table as the server answered for it; null for anything else (the editor then shows nothing editable). */
export function parseEditorTable(
  body: unknown,
  asked: { schema: string; table: string },
): EditorTable | null {
  const answer = doc(body);
  if (answer === null || answer.schema !== asked.schema || answer.table !== asked.table)
    return null;
  const columns = strings(answer.columns);
  const primaryKey = strings(answer.primary_key);
  if (columns === null || primaryKey === null || primaryKey.length === 0) return null;
  if (!primaryKey.every((name) => columns.includes(name))) return null;
  const names = (value: unknown) =>
    Array.isArray(value)
      ? value.flatMap((item: unknown) => {
          const name = doc(item)?.name;
          return typeof name === 'string' ? [name] : [];
        })
      : [];
  const described = new Map(
    (Array.isArray(answer.column_types) ? answer.column_types : []).flatMap((item: unknown) => {
      const column = parseColumnType(item);
      return column !== null && columns.includes(column.name)
        ? [[column.name, column] as const]
        : [];
    }),
  );
  return {
    schema: asked.schema,
    table: asked.table,
    columns,
    columnTypes: new Map(columns.map((name) => [name, described.get(name) ?? untyped(name)])),
    primaryKey,
    triggers: names(answer.triggers),
    rules: names(answer.rules),
    rowSecurity: answer.row_security === true,
    maxChanges:
      typeof answer.max_changes === 'number' && answer.max_changes > 0 ? answer.max_changes : 1000,
  };
}

export async function fetchEditorTable(
  fetcher: typeof fetch,
  connectionId: string,
  schema: string,
  table: string,
): Promise<Result<EditorTable>> {
  const result = await send<unknown>(fetcher, 'POST', '/api/write/data-editor/table', {
    connection_id: connectionId,
    schema,
    table,
  });
  if (!result.ok) return result;
  const parsed = parseEditorTable(result.value, { schema, table });
  return parsed === null
    ? {
        ok: false,
        problem: { code: 'studio.answer_invalid', kind: 'internal', status: 0, logId: null },
      }
    : { ok: true, value: parsed };
}

// --- the changeset ------------------------------------------------------------------------------------------------

export type Row = Record<string, unknown>;

export type Change =
  | { op: 'update'; key: Row; old: Row; new: Row }
  | { op: 'insert'; values: Row }
  | { op: 'delete'; key: Row; old: Row };

/**
 * What the user changed on the loaded page: new cell values by row, the rows to delete and the new rows. A value is
 * what an editor gave: a choice (`true`, an enum label), `null`, or `Typed` text, which `resolveEdits` turns into the
 * column's value.
 */
export interface Edits {
  /** Row index on the page -> column -> the new value. */
  cells: Map<number, Map<string, unknown>>;
  deleted: Set<number>;
  /** New rows: column -> value; a column not set is left to its default. */
  inserted: Row[];
}

export function emptyEdits(): Edits {
  return { cells: new Map(), deleted: new Set(), inserted: [] };
}

/** Same value, telling `1`, `1.0`-as-text and `true` apart (as the server does). */
export function sameValue(first: unknown, second: unknown): boolean {
  return JSON.stringify(first) === JSON.stringify(second);
}

/** Where a cell is: a loaded row (`row`, its index on the page) or a new row (`new`, its index among them). */
export interface CellRef {
  row: { loaded: number } | { added: number };
  column: string;
}

/** A value of the changeset that does not fit its column, from the page's checks or the server's refusal. */
export interface CellIssue extends CellRef {
  code: IssueCode;
}

/**
 * The edits with every value resolved for its column (`Typed` text parsed and checked, a choice checked), and the
 * cells whose value does not fit: only changed cells of loaded rows are checked (an unchanged value is what the viewer
 * showed); a new row's set cells are, and a NOT NULL column without a default that it leaves out is `value.required`.
 * A cell with an issue keeps its loaded value (or is left out of a new row) in the resolved edits.
 */
export function resolveEdits(
  table: Pick<EditorTable, 'columnTypes'>,
  columns: string[],
  rows: unknown[][],
  edits: Edits,
): { edits: Edits; issues: CellIssue[] } {
  const issues: CellIssue[] = [];
  const typeOf = (name: string) => table.columnTypes.get(name) ?? untyped(name);
  const resolve = (
    row: CellRef['row'],
    column: string,
    original: unknown,
    entry: unknown,
  ): { value: unknown } | null => {
    const type = typeOf(column);
    const result =
      entry instanceof Typed
        ? resolveText(type, original, entry.text)
        : ((issue) => (issue === null ? { value: entry } : { issue }))(checkValue(type, entry));
    if ('issue' in result) {
      issues.push({ row, column, code: result.issue });
      return null;
    }
    return result;
  };
  const cells = new Map<number, Map<string, unknown>>();
  for (const [index, changed] of edits.cells) {
    if (edits.deleted.has(index)) continue;
    const resolved = new Map<string, unknown>();
    for (const [column, entry] of changed) {
      const at = columns.indexOf(column);
      const original = at >= 0 ? (rows[index]?.[at] ?? null) : null;
      // Unchanged (also the loaded text typed again): what the viewer showed, not checked.
      if (entry instanceof Typed ? entry.text === fieldText(original) : sameValue(entry, original))
        continue;
      const result = resolve({ loaded: index }, column, original, entry);
      if (result !== null) resolved.set(column, result.value);
    }
    cells.set(index, resolved);
  }
  const inserted = edits.inserted.map((values, index) => {
    const row: Row = {};
    for (const [column, entry] of Object.entries(values)) {
      const result = resolve({ added: index }, column, '', entry);
      if (result !== null) row[column] = result.value;
    }
    for (const type of table.columnTypes.values()) {
      if (!type.optional && !type.nullable && !(type.name in values)) {
        issues.push({ row: { added: index }, column: type.name, code: 'value.required' });
      }
    }
    return row;
  });
  return { edits: { cells, deleted: edits.deleted, inserted }, issues };
}

/**
 * The changes of `edits` on the page `rows` (one value per column of `columns`), in the order update, insert, delete.
 * Every shown column is passed as `old` (review L-1 of tabrion #78); the key is the table's primary key.
 */
export function buildChanges(
  primaryKey: string[],
  columns: string[],
  rows: unknown[][],
  edits: Edits,
): Change[] {
  return buildChangeset(primaryKey, columns, rows, edits).changes;
}

/** `buildChanges` with the row of each change (`origins[i]` is where `changes[i]` comes from). */
export function buildChangeset(
  primaryKey: string[],
  columns: string[],
  rows: unknown[][],
  edits: Edits,
): { changes: Change[]; origins: CellRef['row'][] } {
  const rowOf = (index: number): Row => {
    const row = rows[index] ?? [];
    return Object.fromEntries(columns.map((name, at) => [name, row[at] ?? null]));
  };
  const keyOf = (old: Row): Row => Object.fromEntries(primaryKey.map((name) => [name, old[name]]));
  const changes: Change[] = [];
  const origins: CellRef['row'][] = [];
  for (const [index, cells] of [...edits.cells.entries()].sort((a, b) => a[0] - b[0])) {
    if (edits.deleted.has(index)) continue;
    const old = rowOf(index);
    const next: Row = {};
    for (const [name, value] of cells) {
      if (!sameValue(value, old[name])) next[name] = value;
    }
    if (Object.keys(next).length > 0) {
      changes.push({ op: 'update', key: keyOf(old), old, new: next });
      origins.push({ loaded: index });
    }
  }
  edits.inserted.forEach((values, index) => {
    changes.push({ op: 'insert', values: { ...values } });
    origins.push({ added: index });
  });
  for (const index of [...edits.deleted].sort((a, b) => a - b)) {
    const old = rowOf(index);
    changes.push({ op: 'delete', key: keyOf(old), old });
    origins.push({ loaded: index });
  }
  return { changes, origins };
}

/**
 * The server's issues of a refused changeset (`changes.<change>.<column>`: the change's position in the request and
 * the column's in the table) as cells of the page; issues it cannot place are left out (the problem text still says
 * that a value was refused).
 */
export function serverIssues(
  issues: ProblemIssue[] | undefined,
  origins: CellRef['row'][],
  tableColumns: string[],
): CellIssue[] {
  return (issues ?? []).flatMap((issue) => {
    const found = /^changes\.(\d+)\.(\d+)$/.exec(issue.pointer);
    const row = found ? origins[Number(found[1])] : undefined;
    const column = found ? tableColumns[Number(found[2])] : undefined;
    return row !== undefined && column !== undefined && issue.code.startsWith('value.')
      ? [{ row, column, code: issue.code as IssueCode }]
      : [];
  });
}

// --- the plan: preview, confirmation, execution --------------------------------------------------------------------

export interface PlanStep {
  action: string;
  destructive: boolean;
}

export interface PlanPreview {
  planHash: string;
  steps: PlanStep[];
  destructive: boolean;
  risks: string[];
  confirmText: string;
  triggers: string[];
  rules: string[];
}

export function parsePreview(body: unknown): PlanPreview | null {
  const answer = doc(body);
  if (
    answer === null ||
    typeof answer.plan_hash !== 'string' ||
    !/^[0-9a-f]{64}$/.test(answer.plan_hash)
  ) {
    return null;
  }
  if (typeof answer.confirm_text !== 'string' || !Array.isArray(answer.steps)) return null;
  const steps = answer.steps.flatMap((item: unknown) => {
    const step = doc(item);
    return step !== null && typeof step.action === 'string'
      ? [{ action: step.action, destructive: step.destructive === true }]
      : [];
  });
  const targets = Array.isArray(answer.targets) ? answer.targets.map(doc) : [];
  const names = (key: 'triggers' | 'rules') =>
    targets.flatMap((target) =>
      Array.isArray(target?.[key])
        ? (target[key] as unknown[]).flatMap((item) => {
            const name = doc(item)?.name;
            return typeof name === 'string' ? [name] : [];
          })
        : [],
    );
  return {
    planHash: answer.plan_hash,
    steps,
    destructive: answer.destructive === true,
    risks: strings(answer.risks) ?? [],
    confirmText: answer.confirm_text,
    triggers: names('triggers'),
    rules: names('rules'),
  };
}

export async function previewChangeset(
  fetcher: typeof fetch,
  connectionId: string,
  table: EditorTable,
  columns: string[],
  changes: Change[],
): Promise<Result<PlanPreview>> {
  const result = await send<unknown>(fetcher, 'POST', '/api/write/plans', {
    connection_id: connectionId,
    executor: EXECUTOR,
    request: { schema: table.schema, table: table.table, columns, changes },
  });
  if (!result.ok) return result;
  const preview = parsePreview(result.value);
  return preview === null
    ? {
        ok: false,
        problem: { code: 'studio.answer_invalid', kind: 'internal', status: 0, logId: null },
      }
    : { ok: true, value: preview };
}

export function confirmPlan(
  fetcher: typeof fetch,
  connectionId: string,
  planHash: string,
  typed: string,
): Promise<Result<unknown>> {
  return send(fetcher, 'POST', '/api/write/plans/confirm', {
    connection_id: connectionId,
    plan_hash: planHash,
    typed,
  });
}

export interface PlanResult {
  rows: Record<string, number>;
  auditPending: boolean;
}

export async function executePlan(
  fetcher: typeof fetch,
  connectionId: string,
  planHash: string,
): Promise<Result<PlanResult>> {
  const result = await send<unknown>(fetcher, 'POST', '/api/write/plans/execute', {
    connection_id: connectionId,
    plan_hash: planHash,
  });
  if (!result.ok) return result;
  const answer = doc(result.value);
  const rows = doc(answer?.rows) ?? {};
  return {
    ok: true,
    value: {
      rows: Object.fromEntries(
        Object.entries(rows).filter(
          (entry): entry is [string, number] => typeof entry[1] === 'number',
        ),
      ),
      auditPending: answer?.audit_pending === true,
    },
  };
}

export function discardPlan(fetcher: typeof fetch, planHash: string): Promise<Result<null>> {
  return send(fetcher, 'DELETE', '/api/write/plans', { plan_hash: planHash });
}
