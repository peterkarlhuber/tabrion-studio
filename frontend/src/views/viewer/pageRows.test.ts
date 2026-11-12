/** STU-013 page: the byte bound of a page (`pageLimit`, `observedLimit`) and the page's rows with the search. */
import { rowBytes, valueBytes } from '../editor/rowCache';
import {
  CELL_CHARS,
  observedLimit,
  PAGE_BYTES,
  PageRows,
  pageLimit,
  worstValueBytes,
} from './pageRows';

describe('worstValueBytes', () => {
  it('counts text without a size at the server cap, 4 bytes a character (L-2)', () => {
    expect(worstValueBytes('postgresql', 'TEXT')).toBe(16 + 4 * CELL_CHARS);
    expect(worstValueBytes('sqlite', 'TEXT')).toBe(16 + 4 * CELL_CHARS);
    expect(worstValueBytes('postgresql', 'BYTEA')).toBe(16 + 4 * CELL_CHARS);
    expect(worstValueBytes('postgresql', null)).toBe(16 + 4 * CELL_CHARS);
    expect(worstValueBytes('postgresql', 'NUMERIC')).toBe(16 + 4 * CELL_CHARS);
  });

  it('counts decoded JSON and arrays 16 times (R-1)', () => {
    for (const type of ['JSONB', 'json', 'INTEGER[]', 'ARRAY']) {
      expect(worstValueBytes('postgresql', type)).toBe(16 + 16 * CELL_CHARS);
    }
  });

  it('counts short and sized types by their size', () => {
    for (const type of [
      'BIGINT',
      'integer',
      'TIMESTAMP WITH TIME ZONE',
      'TIMESTAMP(3) WITHOUT TIME ZONE',
      'UUID',
      'BOOLEAN',
      'DOUBLE PRECISION',
    ]) {
      expect(worstValueBytes('postgresql', type)).toBe(96);
    }
    expect(worstValueBytes('postgresql', 'VARCHAR(40)')).toBe(16 + 4 * 42);
    expect(worstValueBytes('postgresql', 'NUMERIC(18, 6)')).toBe(16 + 4 * 20);
    expect(worstValueBytes('postgresql', 'VARCHAR(100000)')).toBe(16 + 4 * CELL_CHARS);
    expect(worstValueBytes('sqlite', 'NUMERIC')).toBe(96);
    expect(worstValueBytes('sqlite', 'INTEGER')).toBe(96);
  });

  // The server cuts text at 65 536 code points; one above U+FFFF is two UTF-16 units in the browser (L-2 of
  // stu-013-page-review.md).
  it('a cut text of characters above U+FFFF stays within the worst case of its column', () => {
    const emoji = '\u{1F600}';
    const cut = emoji.repeat(CELL_CHARS);
    expect(cut.length).toBe(2 * CELL_CHARS);
    expect(valueBytes(cut)).toBeLessThanOrEqual(worstValueBytes('postgresql', 'TEXT'));
    expect(valueBytes(cut)).toBeLessThanOrEqual(worstValueBytes('sqlite', null));
    expect(valueBytes(emoji.repeat(40))).toBeLessThanOrEqual(
      worstValueBytes('postgresql', 'VARCHAR(40)'),
    );
    expect(valueBytes(emoji.repeat(200))).toBeLessThanOrEqual(
      worstValueBytes('sqlite', 'VARCHAR(200)'),
    );
  });
});

describe('pageLimit', () => {
  it('keeps the chosen size for narrow columns', () => {
    expect(pageLimit('postgresql', ['BIGINT', 'VARCHAR(40)', 'DATE'], 1000)).toBe(1000);
    expect(pageLimit('sqlite', ['INTEGER', 'VARCHAR(200)'], 100)).toBe(100);
  });

  it('asks for fewer rows when the widest page would pass 64 MB', () => {
    // 20 text columns: one row can take 20 * 256 KB, so 12 rows fill 64 MB.
    const types = Array.from({ length: 20 }, () => 'TEXT');
    const limit = pageLimit('postgresql', types, 1000);
    expect(limit).toBe(12);
    const row = 64 + 20 * (16 + 4 * CELL_CHARS);
    expect(limit * row).toBeLessThanOrEqual(PAGE_BYTES);
    expect((limit + 1) * row).toBeGreaterThan(PAGE_BYTES);
    expect(pageLimit('postgresql', ['BIGINT', 'JSONB'], 100)).toBe(63);
  });

  it('asks for at least one row', () => {
    const types = Array.from({ length: 1000 }, () => 'JSONB');
    expect(pageLimit('postgresql', types, 100)).toBe(1);
  });
});

describe('observedLimit', () => {
  it('keeps the limit until a row was seen, then fits the widest row seen', () => {
    expect(observedLimit(0, 500)).toBe(500);
    expect(observedLimit(1024 * 1024, 500)).toBe(64);
    expect(observedLimit(1000, 500)).toBe(500);
    expect(observedLimit(PAGE_BYTES * 2, 500)).toBe(1);
  });
});

describe('PageRows', () => {
  const values = [
    [1, 'Steel', null],
    [2, 'Aluminium', { base64: 'AA==' }],
    [3, 'Stainless steel', ['a']],
  ];

  it('holds the page for the grid, with the cut cells', () => {
    const rows = new PageRows(values, [[1, 1]]);
    expect(rows.rowCount).toBe(3);
    expect(rows.pageRowCount).toBe(3);
    expect(rows.get(1)?.values).toEqual(values[1]);
    expect(rows.get(1)?.cut.has(1)).toBe(true);
    expect(rows.get(0)?.cut.size).toBe(0);
    expect(rows.get(3)).toBeUndefined();
    expect(rows.bytes).toBe(values.reduce((sum, row) => sum + rowBytes(row), 0));
    const unsubscribe = rows.subscribe();
    expect(unsubscribe()).toBeUndefined();
  });

  it('searches the page without case and maps a shown row to the page row', () => {
    const rows = new PageRows(values, [], '  STEEL ');
    expect(rows.rowCount).toBe(2);
    expect(rows.pageRowCount).toBe(3);
    expect(rows.get(1)?.values[0]).toBe(3);
    expect(rows.pageIndex(0)).toBe(0);
    expect(rows.pageIndex(1)).toBe(2);
    expect(rows.pageIndex(2)).toBeUndefined();
    // NULL is not the text "null".
    expect(new PageRows(values, [], 'null').rowCount).toBe(0);
    expect(new PageRows(values, [], 'a').rowCount).toBe(2);
  });
});
