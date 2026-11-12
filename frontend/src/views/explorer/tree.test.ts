/** STU-010: the rows of the explorer tree, its filter and the path to an object. */
import { parseCatalog, type Catalog } from '../../api/explorer';
import { PG_CATALOG, SQLITE_CATALOG } from '../../harness/explorer';
import {
  buildRows,
  EXTENSIONS_ID,
  FILE_ID,
  groupId,
  initialExpanded,
  matches,
  NO_FILTER,
  objectId,
  pathTo,
  schemaId,
} from './tree';

const pg = parseCatalog(PG_CATALOG) as Catalog;
const sqlite = parseCatalog(SQLITE_CATALOG) as Catalog;

describe('buildRows', () => {
  it('PostgreSQL: schemas, then groups and objects of the open ones, then the extensions', () => {
    const { rows } = buildRows(pg, initialExpanded(pg), NO_FILTER);
    expect(rows.map((r) => [r.type, r.level, r.posinset, r.setsize])).toEqual([
      ['schema', 1, 1, 3],
      ['schema', 1, 2, 3],
      ['group', 2, 1, 4],
      ['object', 3, 1, 3],
      ['object', 3, 2, 3],
      ['object', 3, 3, 3],
      ['group', 2, 2, 4],
      ['group', 2, 3, 4],
      ['group', 2, 4, 4],
      ['extensions', 1, 3, 3],
    ]);
    expect(rows[0]?.expanded).toBeUndefined(); // the empty schema is a leaf
    expect(rows[3]?.parent).toBe(groupId('public', 'table'));
    expect(rows[2]?.parent).toBe(schemaId('public'));
    expect(rows.at(-1)?.id).toBe(EXTENSIONS_ID);
  });

  it('SQLite: the file, then the groups of main on the first level', () => {
    const { rows } = buildRows(sqlite, initialExpanded(sqlite), NO_FILTER);
    expect(rows[0]?.id).toBe(FILE_ID);
    expect(rows.filter((r) => r.level === 1).map((r) => r.type)).toEqual([
      'file',
      'group',
      'group',
    ]);
    expect(rows.find((r) => r.type === 'object')?.parent).toBe(groupId('main', 'table'));
  });

  it('ids cannot collide, whatever the names hold', () => {
    expect(objectId('a', 'b:c')).not.toBe(objectId('a:b', 'c'));
    expect(schemaId('x')).not.toBe(groupId('x', 'table'));
  });

  it('a filter shows only the matches, with their groups open, and no file or extensions', () => {
    const { rows, matched } = buildRows(sqlite, new Set(), { query: 'mat', kind: 'all' });
    expect(rows.map((r) => r.type)).toEqual(['group', 'object']);
    expect(rows[0]?.expanded).toBe(true);
    expect(matched).toBe(1);
    expect(
      buildRows(pg, new Set(), { query: '', kind: 'sequence' }).rows.map((r) => r.type),
    ).toEqual(['schema', 'group', 'object']);
  });
});

describe('matches', () => {
  const object = { schema: 'public', name: 'Measurement', kind: 'table' as const };
  it.each([
    ['meas', true],
    ['  MEAS ', true],
    ['urement', false], // a prefix, not a substring (03 section 2 B)
    ['public.me', true],
    ['pub', false],
    ['', true],
  ])('"%s" → %s', (query, expected) => {
    expect(matches(object, { query, kind: 'all' })).toBe(expected);
  });

  it('and the type', () => {
    expect(matches(object, { query: '', kind: 'view' })).toBe(false);
    expect(matches(object, { query: 'm', kind: 'table' })).toBe(true);
  });
});

describe('pathTo', () => {
  it('opens the schema and the group (only the group on SQLite)', () => {
    const device = { schema: 'public', name: 'device', kind: 'table' as const };
    expect(pathTo(pg, device)).toEqual([schemaId('public'), groupId('public', 'table')]);
    const part = { schema: 'main', name: 'part', kind: 'table' as const };
    expect(pathTo(sqlite, part)).toEqual([groupId('main', 'table')]);
  });
});
