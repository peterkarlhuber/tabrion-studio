import type { Catalog } from '../../api/explorer';
import { columnKey, namespaceOf, qualifierBefore, relationNamed } from './completion';
import { formatSql } from './formatSql';

const catalog: Catalog = {
  connectionId: 'c1',
  engine: 'postgresql',
  schemas: ['public', 'audit', 'empty'],
  objects: [
    { schema: 'public', name: 'parts', kind: 'table' },
    { schema: 'audit', name: 'parts', kind: 'table' },
    { schema: 'public', name: 'parts_view', kind: 'view' },
    { schema: 'public', name: 'parts_seq', kind: 'sequence' },
    { schema: 'audit', name: 'Mixed', kind: 'materialized_view' },
  ],
  builtAt: null,
  cached: true,
};

describe('completion from the catalog (STU-020)', () => {
  it('builds the namespace of tables and views by schema, without sequences', () => {
    const columns = new Map([[columnKey({ schema: 'public', name: 'parts' }), ['id', 'label']]]);
    expect(namespaceOf(catalog, columns)).toEqual({
      public: {
        parts: [
          { label: 'id', type: 'property' },
          { label: 'label', type: 'property' },
        ],
        parts_view: [],
      },
      audit: { parts: [], Mixed: [] },
      empty: {},
    });
  });

  it('finds the qualifier before the cursor', () => {
    expect(qualifierBefore('select parts.')).toEqual({ qualifier: 'parts', from: 13 });
    expect(qualifierBefore('select p.la')).toEqual({ qualifier: 'p', from: 9 });
    expect(qualifierBefore('select parts')).toBeNull();
    // A schema-qualified name and a quoted name are CodeMirror's own completion.
    expect(qualifierBefore('select public.parts.')).toBeNull();
    expect(qualifierBefore('select "parts".')).toBeNull();
    expect(qualifierBefore('select 1.')).toBeNull();
  });

  it('resolves a name in the default schema first; PostgreSQL folds unquoted names, SQLite ignores case', () => {
    expect(relationNamed(catalog, 'parts', 'postgresql')?.schema).toBe('public');
    expect(relationNamed(catalog, 'PARTS', 'postgresql')?.schema).toBe('public');
    expect(relationNamed(catalog, 'mixed', 'postgresql')).toBeNull();
    expect(relationNamed(catalog, 'Mixed', 'postgresql')?.schema).toBe('audit');
    expect(relationNamed(catalog, 'parts_seq', 'postgresql')).toBeNull();
    const sqlite: Catalog = {
      ...catalog,
      engine: 'sqlite',
      schemas: ['main'],
      objects: [{ schema: 'main', name: 'Parts', kind: 'table' }],
    };
    expect(relationNamed(sqlite, 'parts', 'sqlite')?.name).toBe('Parts');
  });
});

describe('formatSql (sql-formatter)', () => {
  it('keeps :name placeholders, casts, strings and comments', async () => {
    expect(
      await formatSql("select a::int from t where id = :id and x = ':no' -- keep :z", 'postgresql'),
    ).toBe("SELECT\n  a::int\nFROM\n  t\nWHERE\n  id = :id\n  AND x = ':no' -- keep :z");
    expect(await formatSql('select a from t where b = :b', 'sqlite')).toBe(
      'SELECT\n  a\nFROM\n  t\nWHERE\n  b = :b',
    );
  });

  it('answers null for a text it cannot parse', async () => {
    expect(await formatSql('select (((', 'postgresql')).toBeNull();
  });
});
