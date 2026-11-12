import { describe, expect, it } from 'vitest';

import { placeholders, splitStatements, statementAt } from './sqlScan';

const sqls = (text: string, dialect: 'postgresql' | 'sqlite' = 'postgresql') =>
  splitStatements(text, dialect).map((s) => s.sql);

describe('splitStatements', () => {
  it('splits at semicolons and drops empty statements and comment-only ones', () => {
    expect(sqls('select 1;  ;\nselect 2;\n-- done\n')).toEqual(['select 1', 'select 2']);
    expect(sqls('')).toEqual([]);
    expect(sqls('/* only */ -- comments')).toEqual([]);
  });

  it('keeps semicolons inside literals, identifiers, comments and dollar quotes', () => {
    expect(
      sqls(
        `select 'a;b', "c;d" from t -- x;y\n; select $f$ ; $f$, $$;$$; select E'\\';' ; select /* ; /* ; */ ; */ 1`,
      ),
    ).toEqual([
      `select 'a;b', "c;d" from t -- x;y\n`,
      'select $f$ ; $f$, $$;$$',
      "select E'\\';'",
      'select /* ; /* ; */ ; */ 1',
    ]);
  });

  it('reads SQLite: brackets and backticks quote, block comments do not nest', () => {
    expect(sqls('select [a;b], `c;d`; select /* /* */ 2', 'sqlite')).toEqual([
      'select [a;b], `c;d`',
      'select /* /* */ 2',
    ]);
  });

  it('gives the offsets of each statement in the text', () => {
    const text = 'select 1;\n  select 2';
    const [first, second] = splitStatements(text, 'postgresql');
    expect(text.slice(first?.from, first?.to)).toBe('select 1');
    expect(text.slice(second?.from, second?.to)).toBe('select 2');
  });
});

describe('placeholders', () => {
  it('finds :name, not casts, literals, comments or $1', () => {
    expect(
      placeholders(
        'select :a, x::int, \':b\', "c:d", $1 -- :e\n, :a, :f_2 /* :g */, $$ :h $$ from t where y = :Z',
        'postgresql',
      ),
    ).toEqual(['a', 'f_2', 'Z']);
  });

  it('takes names like the server (L-3 of stu-012-review.md)', () => {
    const long = 'n'.repeat(63);
    expect(placeholders(`select :${long}, :${long}x`, 'postgresql')).toEqual([long]);
    // A name that runs on into `$` or a non-ASCII character is no placeholder.
    expect(placeholders('select :a$b, :größe, :ok', 'postgresql')).toEqual(['ok']);
    // `--` ends at a carriage return too.
    expect(placeholders('select 1 -- note\r:after', 'sqlite')).toEqual(['after']);
    // A non-ASCII word followed by `$x$` is one word, not a dollar quote.
    expect(placeholders('select ä$x$ :p $x$', 'postgresql')).toEqual(['p']);
  });

  it('lists the placeholders per statement', () => {
    expect(splitStatements('select :a; select :b, :a', 'sqlite').map((s) => s.params)).toEqual([
      ['a'],
      ['b', 'a'],
    ]);
  });
});

describe('statementAt', () => {
  const text = 'select 1;\n\nselect 2;';
  const statements = splitStatements(text, 'postgresql');
  it('is the statement under the cursor, the one before it between two, the first before any', () => {
    expect(statementAt(statements, 3)?.sql).toBe('select 1');
    expect(statementAt(statements, 8)?.sql).toBe('select 1');
    expect(statementAt(statements, 10)?.sql).toBe('select 1');
    expect(statementAt(statements, 15)?.sql).toBe('select 2');
    expect(statementAt(statements, text.length)?.sql).toBe('select 2');
    expect(statementAt(splitStatements('  select 3', 'postgresql'), 0)?.sql).toBe('select 3');
    expect(statementAt([], 0)).toBeNull();
  });
});
