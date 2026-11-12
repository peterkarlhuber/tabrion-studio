/** TD-041: the statement the explorer hands to the SQL editor. */
import { handToEditor, quoteIdentifier, selectStatement, takeHandoff } from './handoff';

describe('handoff', () => {
  it('is taken once', () => {
    expect(takeHandoff()).toBeNull();
    handToEditor({ profileId: 'p', title: 't', sql: 'SELECT 1;' });
    expect(takeHandoff()).toEqual({ profileId: 'p', title: 't', sql: 'SELECT 1;' });
    expect(takeHandoff()).toBeNull();
  });

  it('quotes identifiers so that no name ends the quote', () => {
    expect(quoteIdentifier('part')).toBe('"part"');
    expect(quoteIdentifier('a"b')).toBe('"a""b"');
    expect(quoteIdentifier('x"; DROP TABLE y; --')).toBe('"x""; DROP TABLE y; --"');
  });

  it('builds SELECT … LIMIT 100 for each engine', () => {
    expect(selectStatement('postgresql', 'public', 'measurement')).toBe(
      'SELECT * FROM "public"."measurement" LIMIT 100;',
    );
    expect(selectStatement('sqlite', 'main', 'part')).toBe('SELECT * FROM "part" LIMIT 100;');
    expect(selectStatement('sqlite', 'aux', 'part')).toBe('SELECT * FROM "aux"."part" LIMIT 100;');
    expect(selectStatement('postgresql', 'my"schema', 'n\nx', 5)).toBe(
      'SELECT * FROM "my""schema"."n\nx" LIMIT 5;',
    );
  });
});
