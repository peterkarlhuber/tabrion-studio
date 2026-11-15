/** STU-026: the Data Editor's client side, without a page: the changeset it builds and the answers it accepts. */
import {
  buildChanges,
  editableValue,
  emptyEdits,
  EXECUTOR,
  parseEditorTable,
  parsePreview,
  parseUnlocks,
  previewChangeset,
  sameValue,
  typedValue,
  type EditorTable,
} from './dataEditor';

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
  it('keeps a number a number and a boolean a boolean when the text reads as one', () => {
    expect(typedValue(3, ' 42 ')).toBe(42);
    expect(typedValue(3, '4x')).toBe('4x');
    expect(typedValue(3, '')).toBe('');
    expect(typedValue(true, 'false')).toBe(false);
    expect(typedValue(false, 'yes')).toBe('yes');
    expect(typedValue('7', '8')).toBe('8');
    expect(typedValue(null, '8')).toBe('8');
  });

  it('tells 1, "1" and true apart', () => {
    expect(sameValue(1, '1')).toBe(false);
    expect(sameValue(1, true)).toBe(false);
    expect(sameValue(null, '')).toBe(false);
    expect(sameValue({ base64: 'AA==' }, { base64: 'AA==' })).toBe(true);
  });

  it('edits text, numbers, booleans and NULL only (type editors are STU-XDE-1)', () => {
    expect([null, 'a', 1, false].every(editableValue)).toBe(true);
    expect(editableValue({ base64: 'AA==' })).toBe(false);
    expect(editableValue([1])).toBe(false);
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
      primaryKey: ['id'],
      triggers: ['audit_parts'],
      rules: [],
      rowSecurity: false,
      maxChanges: 500,
    });
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
