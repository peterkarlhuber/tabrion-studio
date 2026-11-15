/**
 * STU-026 and STU-XDE-1: the Data Editor's client side, without a page: the changeset it builds, the values it checks
 * and resolves for their columns, and the answers it accepts.
 */
import { editorKind, Typed, untyped, untypedValue, type ColumnType } from './cellValues';
import {
  appendRows,
  buildChanges,
  buildChangeset,
  changesetBytes,
  emptyEdits,
  firstRows,
  EXECUTOR,
  parseEditorTable,
  parsePreview,
  parseUnlocks,
  previewChangeset,
  resolveEdits,
  sameValue,
  serverIssues,
  type EditorTable,
} from './dataEditor';
import type { ViewerPage } from './viewer';

const COLUMNS = ['id', 'name', 'qty'];
const ROWS = [
  [1, 'bolt', 3],
  [2, 'nut', null],
  [3, 'washer', 7],
];

describe('buildChanges', () => {
  it('passes every shown column as old, the key from it, in the order update, insert, delete', () => {
    const edits = emptyEdits();
    edits.cells.set(1, new Map<string, unknown>([['qty', 5]]));
    edits.cells.set(0, new Map<string, unknown>([['name', 'bolt M4']]));
    edits.inserted.push({ name: 'pin' });
    edits.deleted.add(2);
    expect(buildChanges(['id'], COLUMNS, ROWS, edits)).toEqual([
      {
        op: 'update',
        key: { id: 1 },
        old: { id: 1, name: 'bolt', qty: 3 },
        new: { name: 'bolt M4' },
      },
      // A NULL old value is sent as null, never left out (03 section 8).
      { op: 'update', key: { id: 2 }, old: { id: 2, name: 'nut', qty: null }, new: { qty: 5 } },
      { op: 'insert', values: { name: 'pin' } },
      { op: 'delete', key: { id: 3 }, old: { id: 3, name: 'washer', qty: 7 } },
    ]);
  });

  it('drops a cell set back to its loaded value and the edits of a deleted row', () => {
    const edits = emptyEdits();
    edits.cells.set(0, new Map<string, unknown>([['qty', 3]]));
    edits.cells.set(2, new Map<string, unknown>([['qty', 8]]));
    edits.deleted.add(2);
    expect(buildChanges(['id'], COLUMNS, ROWS, edits)).toEqual([
      { op: 'delete', key: { id: 3 }, old: { id: 3, name: 'washer', qty: 7 } },
    ]);
  });

  it('builds a compound key in the order of the primary key', () => {
    const edits = emptyEdits();
    edits.deleted.add(0);
    expect(buildChanges(['name', 'id'], COLUMNS, ROWS, edits)).toEqual([
      { op: 'delete', key: { name: 'bolt', id: 1 }, old: { id: 1, name: 'bolt', qty: 3 } },
    ]);
  });

  it('sends nothing for no edits', () => {
    expect(buildChanges(['id'], COLUMNS, ROWS, emptyEdits())).toEqual([]);
  });
});

describe('values', () => {
  it('keeps a number a number and a boolean a boolean when the text reads as one (untyped columns)', () => {
    expect(untypedValue(3, ' 42 ')).toBe(42);
    expect(untypedValue(3, '4x')).toBe('4x');
    expect(untypedValue(3, '')).toBe('');
    expect(untypedValue(true, 'false')).toBe(false);
    expect(untypedValue(false, 'yes')).toBe('yes');
    expect(untypedValue('7', '8')).toBe('8');
    expect(untypedValue(null, '8')).toBe('8');
  });

  it('tells 1, "1" and true apart', () => {
    expect(sameValue(1, '1')).toBe(false);
    expect(sameValue(1, true)).toBe(false);
    expect(sameValue(null, '')).toBe(false);
    expect(sameValue({ base64: 'AA==' }, { base64: 'AA==' })).toBe(true);
  });

  it('edits text, numbers, booleans and NULL of an untyped column as text, bytes by a file', () => {
    const column = untyped('x');
    expect([null, 'a', 1, false].map((value) => editorKind(column, value))).toEqual([
      'text',
      'text',
      'text',
      'text',
    ]);
    // G-S2 6A: an untyped (SQLite) column that holds bytes is replaced by a file, never typed.
    expect(editorKind(column, { base64: 'AA==' })).toBe('file');
    expect(editorKind(column, [1])).toBe('readOnly');
  });
});

const typed = (kind: ColumnType['kind'], extra: Partial<ColumnType> = {}): ColumnType => ({
  ...untyped('x'),
  kind,
  ...extra,
});

describe('resolveEdits (STU-XDE-1)', () => {
  const table = {
    columnTypes: new Map<string, ColumnType>([
      ['id', { ...typed('int4'), name: 'id', nullable: false }],
      ['name', { ...typed('text', { length: 5 }), name: 'name', nullable: false, optional: false }],
      ['qty', { ...typed('int2'), name: 'qty' }],
    ]),
  };

  it("turns typed text into the column's value and checks it; unchanged cells are not checked", () => {
    const edits = emptyEdits();
    edits.cells.set(0, new Map<string, unknown>([['qty', new Typed(' 12 ')]]));
    edits.cells.set(
      1,
      new Map<string, unknown>([
        ['qty', new Typed('40000')],
        ['name', new Typed('nut')],
      ]),
    );
    edits.cells.set(2, new Map<string, unknown>([['name', null]]));
    const { edits: resolved, issues } = resolveEdits(table, COLUMNS, ROWS, edits);
    expect(resolved.cells.get(0)).toEqual(new Map([['qty', 12]]));
    // The loaded text typed again is no change; a refused value keeps the loaded one.
    expect(resolved.cells.get(1)).toEqual(new Map());
    expect(issues).toEqual([
      { row: { loaded: 1 }, column: 'qty', code: 'value.out_of_range' },
      { row: { loaded: 2 }, column: 'name', code: 'value.not_null' },
    ]);
    expect(buildChanges(['id'], COLUMNS, ROWS, resolved)).toEqual([
      { op: 'update', key: { id: 1 }, old: { id: 1, name: 'bolt', qty: 3 }, new: { qty: 12 } },
    ]);
  });

  it("checks a new row's values and names the required columns it leaves out", () => {
    const edits = emptyEdits();
    edits.inserted.push({ qty: new Typed('1.5') }, { name: new Typed('pin'), qty: null });
    const { edits: resolved, issues } = resolveEdits(table, COLUMNS, ROWS, edits);
    expect(resolved.inserted).toEqual([{}, { name: 'pin', qty: null }]);
    expect(issues).toEqual([
      { row: { added: 0 }, column: 'qty', code: 'value.format' },
      { row: { added: 0 }, column: 'name', code: 'value.required' },
    ]);
  });

  it('skips the edits of a deleted row', () => {
    const edits = emptyEdits();
    edits.cells.set(0, new Map<string, unknown>([['qty', new Typed('x')]]));
    edits.deleted.add(0);
    expect(resolveEdits(table, COLUMNS, ROWS, edits).issues).toEqual([]);
  });
});

describe("the server's issues", () => {
  it('are placed at the row of their change and the column at their position in the table', () => {
    const edits = emptyEdits();
    edits.cells.set(1, new Map<string, unknown>([['qty', 5]]));
    edits.inserted.push({ name: 'pin' });
    edits.deleted.add(2);
    const { origins } = buildChangeset(['id'], COLUMNS, ROWS, edits);
    expect(origins).toEqual([{ loaded: 1 }, { added: 0 }, { loaded: 2 }]);
    expect(
      serverIssues(
        [
          { pointer: 'changes.0.2', code: 'value.out_of_range' },
          { pointer: 'changes.1.1', code: 'value.too_long' },
          { pointer: 'changes.9.1', code: 'value.format' },
          { pointer: 'changes.0.9', code: 'value.format' },
          { pointer: 'profile.host', code: 'config.required' },
        ],
        origins,
        COLUMNS,
      ),
    ).toEqual([
      { row: { loaded: 1 }, column: 'qty', code: 'value.out_of_range' },
      { row: { added: 0 }, column: 'name', code: 'value.too_long' },
    ]);
    expect(serverIssues(undefined, origins, COLUMNS)).toEqual([]);
  });

  it("place the run's constraint refusal at its cell, or at its row without a column (G-S2 6A)", () => {
    const origins = [{ loaded: 1 }, { added: 0 }, { loaded: 2 }];
    expect(
      serverIssues(
        [
          { pointer: 'changes.1.1', code: 'constraint.unique' },
          { pointer: 'changes.2', code: 'constraint.foreign_key' },
          { pointer: 'changes.0', code: 'value.format' }, // a value issue always names its column
          { pointer: 'changes.0', code: 'constraint.other' },
          { pointer: 'changes.7', code: 'constraint.check' },
        ],
        origins,
        COLUMNS,
      ),
    ).toEqual([
      { row: { added: 0 }, column: 'name', code: 'constraint.unique' },
      { row: { loaded: 2 }, column: null, code: 'constraint.foreign_key' },
    ]);
  });
});

describe('loading more rows (G-S2 6A)', () => {
  const page = (rows: unknown[][], next: string | null, cut: [number, number][] = []) =>
    ({
      columns: COLUMNS.map((name) => ({ name, type: '' })),
      rows,
      truncatedCells: cut,
      cutByBytes: false,
      page: 'p',
      next,
      paging: 'primary_key',
      hint: null,
    }) as ViewerPage;

  it('appends the next page, so every loaded row keeps its index and its edits', () => {
    const first = firstRows(page(ROWS.slice(0, 2), 'n1', [[1, 2]]));
    expect(first.cut).toEqual(new Set([1]));
    const more = appendRows(first, page([ROWS[2] as unknown[]], null, [[0, 1]]), ['id']);
    expect(more?.rows).toEqual(ROWS);
    expect(more?.cut).toEqual(new Set([1, 2]));
    expect(more?.next).toBeNull();
    // An edit of row 1 stays with the nut; an edit of the appended row 2 is the washer's.
    const edits = emptyEdits();
    edits.cells.set(1, new Map<string, unknown>([['qty', 5]]));
    edits.deleted.add(2);
    expect(buildChanges(['id'], COLUMNS, more?.rows ?? [], edits)).toEqual([
      { op: 'update', key: { id: 2 }, old: { id: 2, name: 'nut', qty: null }, new: { qty: 5 } },
      { op: 'delete', key: { id: 3 }, old: { id: 3, name: 'washer', qty: 7 } },
    ]);
  });

  it('leaves out a row whose key is already loaded, and refuses a page of other columns', () => {
    const first = firstRows(page(ROWS.slice(0, 2), 'n1'));
    const moved = appendRows(first, page([[2, 'nut M4', 1], ROWS[2] as unknown[]], null), ['id']);
    expect(moved?.rows).toEqual(ROWS);
    const other = page([[4, 'pin', 1]], null);
    other.columns = [...other.columns, { name: 'extra', type: '' }];
    expect(appendRows(first, other, ['id'])).toBeNull();
  });
});

describe('the size of a changeset', () => {
  it('counts as the server does: JSON with every non-ASCII character escaped', () => {
    expect(changesetBytes({ a: 'x' })).toBe('{"a":"x"}'.length);
    expect(changesetBytes({ a: 'ä' })).toBe('{"a":"\\u00e4"}'.length);
    expect(changesetBytes({ a: '😀' })).toBe('{"a":"\\ud83d\\ude00"}'.length);
  });
});

describe('answers', () => {
  it('reads unlocks defensively', () => {
    expect(parseUnlocks(null)).toEqual([]);
    expect(
      parseUnlocks({
        unlocks: [
          {
            connection_id: 'c1',
            write_profile_id: 'write:w',
            environment: 'dev',
            expires_in_s: 60,
          },
          { connection_id: 2 },
          null,
        ],
      }),
    ).toEqual([
      { connectionId: 'c1', writeProfileId: 'write:w', environment: 'dev', expiresInS: 60 },
    ]);
  });

  it('accepts the table it asked for, with a primary key among its columns, only', () => {
    const answer = {
      schema: 'main',
      table: 'parts',
      columns: ['id', 'name'],
      primary_key: ['id'],
      triggers: [{ name: 'audit_parts' }, { nope: 1 }],
      rules: [],
      row_security: false,
      max_changes: 500,
    };
    const asked = { schema: 'main', table: 'parts' };
    expect(parseEditorTable(answer, asked)).toEqual({
      schema: 'main',
      table: 'parts',
      columns: ['id', 'name'],
      // An answer without column types: every column is untyped (the database checks it).
      columnTypes: new Map([
        ['id', untyped('id')],
        ['name', untyped('name')],
      ]),
      primaryKey: ['id'],
      triggers: ['audit_parts'],
      rules: [],
      rowSecurity: false,
      maxChanges: 500,
    });
    const withTypes = parseEditorTable(
      {
        ...answer,
        column_types: [
          {
            name: 'id',
            kind: 'int4',
            nullable: false,
            optional: true,
            read_only: false,
            array: false,
          },
          { name: 'ghost', kind: 'text' },
          { name: 'name', kind: 'nonsense' },
        ],
      },
      asked,
    );
    expect(withTypes?.columnTypes.get('id')).toEqual({
      ...typed('int4'),
      name: 'id',
      nullable: false,
    });
    expect(withTypes?.columnTypes.get('name')).toEqual(untyped('name'));
    expect(withTypes?.columnTypes.has('ghost')).toBe(false);
    expect(parseEditorTable(answer, { schema: 'main', table: 'other' })).toBeNull();
    expect(parseEditorTable({ ...answer, primary_key: [] }, asked)).toBeNull();
    expect(parseEditorTable({ ...answer, primary_key: ['ghost'] }, asked)).toBeNull();
    expect(parseEditorTable({ ...answer, columns: 'id' }, asked)).toBeNull();
  });

  it('accepts a preview with a plan hash and a confirmation text only', () => {
    const hash = 'a'.repeat(64);
    expect(
      parsePreview({
        plan_hash: hash,
        confirm_text: 'parts',
        destructive: true,
        risks: ['x'],
        steps: [{ action: 'row.delete', destructive: true }, { action: 1 }],
        targets: [{ triggers: [{ name: 't1' }], rules: [{ name: 'r1' }] }],
      }),
    ).toEqual({
      planHash: hash,
      steps: [{ action: 'row.delete', destructive: true }],
      destructive: true,
      risks: ['x'],
      confirmText: 'parts',
      triggers: ['t1'],
      rules: ['r1'],
    });
    expect(parsePreview({ plan_hash: 'nope', confirm_text: 'x', steps: [] })).toBeNull();
    expect(parsePreview({ plan_hash: hash, steps: [] })).toBeNull();
  });
});

describe('previewChangeset', () => {
  it('sends the changeset as a plan of the Data Editor executor', async () => {
    const bodies: unknown[] = [];
    const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe('/api/write/plans');
      bodies.push(JSON.parse(String(init?.body)));
      return new Response(
        JSON.stringify({ plan_hash: 'b'.repeat(64), confirm_text: 'w', steps: [] }),
      );
    }) as typeof fetch;
    const table: EditorTable = {
      schema: 'main',
      table: 'parts',
      columns: COLUMNS,
      columnTypes: new Map(),
      primaryKey: ['id'],
      triggers: [],
      rules: [],
      rowSecurity: false,
      maxChanges: 1000,
    };
    const changes = [{ op: 'insert' as const, values: { name: 'pin' } }];
    const result = await previewChangeset(fetcher, 'c1', table, COLUMNS, changes);
    expect(result.ok).toBe(true);
    expect(bodies).toEqual([
      {
        connection_id: 'c1',
        executor: EXECUTOR,
        request: { schema: 'main', table: 'parts', columns: COLUMNS, changes },
      },
    ]);
  });
});
