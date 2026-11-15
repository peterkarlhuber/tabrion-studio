/**
 * STU-XDE-1: the page's value checks run the same vectors as the server's (`cellValues.vectors.json`, also read by
 * tests/security/test_data_editor_values.py), and typed text becomes the value of its column.
 */
import {
  checkValue,
  editorKind,
  fieldText,
  parseColumnType,
  resolveText,
  Typed,
  typeLabel,
  untyped,
  type ColumnType,
} from './cellValues';
import vectors from './cellValues.vectors.json';

interface Case {
  column: Record<string, unknown>;
  value: unknown;
  issue: string | null;
}

const cases = (vectors as { cases: Case[] }).cases;

describe('the shared vectors', () => {
  it.each(cases.map((item, index) => [index, item] as const))('case %i', (_index, item) => {
    const column = parseColumnType(item.column);
    expect(column).not.toBeNull();
    expect(checkValue(column as ColumnType, item.value)).toBe(item.issue);
  });
});

const column = (kind: ColumnType['kind'], extra: Partial<ColumnType> = {}): ColumnType => ({
  ...untyped('c'),
  kind,
  ...extra,
});

describe('resolveText', () => {
  it('makes numbers of whole numbers JavaScript holds exactly, and text of the rest', () => {
    expect(resolveText(column('int4'), 1, ' 42 ')).toEqual({ value: 42 });
    expect(resolveText(column('int8'), 1, '9223372036854775807')).toEqual({
      value: '9223372036854775807',
    });
    expect(resolveText(column('int4'), 1, '4.5')).toEqual({ issue: 'value.format' });
    expect(resolveText(column('int2'), 1, '70000')).toEqual({ issue: 'value.out_of_range' });
  });

  it('keeps decimals, dates, times and UUIDs as text, trimmed, and text as typed', () => {
    const numeric = column('numeric', { precision: 5, scale: 2 });
    expect(resolveText(numeric, '1.00', ' 12.50 ')).toEqual({ value: '12.50' });
    expect(resolveText(numeric, '1.00', '1234')).toEqual({ issue: 'value.out_of_range' });
    expect(resolveText(column('float8'), 1, '1.')).toEqual({ value: 1 });
    expect(resolveText(column('float8'), 1, 'NaN')).toEqual({ value: 'NaN' });
    expect(resolveText(column('date'), null, '2025-05-06')).toEqual({ value: '2025-05-06' });
    expect(resolveText(column('date'), null, '06.05.2025')).toEqual({ issue: 'value.format' });
    expect(resolveText(column('timestamptz'), null, '2025-01-01 10:00')).toEqual({
      issue: 'value.format',
    });
    expect(resolveText(column('text'), '', '  padded  ')).toEqual({ value: '  padded  ' });
    expect(resolveText(column('text'), 'a', '')).toEqual({ value: '' });
    expect(resolveText(column('text', { length: 2 }), '', 'abc')).toEqual({
      issue: 'value.too_long',
    });
  });

  it('parses JSON and arrays; a JSON null is refused (NULL is the button)', () => {
    expect(resolveText(column('json'), null, '{"a": [1, 2]}')).toEqual({ value: { a: [1, 2] } });
    expect(resolveText(column('json'), null, '"text"')).toEqual({ value: 'text' });
    expect(resolveText(column('json'), null, '{"a": ')).toEqual({ issue: 'value.format' });
    expect(resolveText(column('json'), null, 'null')).toEqual({ issue: 'value.json_null' });
    const tags = column('text', { array: true, length: 3 });
    expect(resolveText(tags, null, '[["ab", "c"], [null, "d"]]')).toEqual({
      value: [
        ['ab', 'c'],
        [null, 'd'],
      ],
    });
    expect(resolveText(tags, null, '["abcd"]')).toEqual({ issue: 'value.too_long' });
    expect(resolveText(tags, null, '{ab,c}')).toEqual({ issue: 'value.format' });
    expect(resolveText(column('int4', { array: true }), null, '[1, 2.5]')).toEqual({
      issue: 'value.type',
    });
  });

  it('keeps the rule of STU-026 for untyped columns', () => {
    expect(resolveText(untyped('c'), 3, '4')).toEqual({ value: 4 });
    expect(resolveText(untyped('c'), 'x', '4')).toEqual({ value: '4' });
  });
});

describe('editors and labels', () => {
  it('chooses the editor by the column', () => {
    expect(editorKind(column('boolean'), true)).toBe('boolean');
    expect(editorKind(column('enum', { values: ['a'] }), 'a')).toBe('enum');
    expect(editorKind(column('json'), { a: 1 })).toBe('text');
    expect(editorKind(column('int4', { array: true }), [1])).toBe('text');
    expect(editorKind(column('bytes'), { base64: 'AA==' })).toBe('readOnly');
    expect(editorKind(column('int4', { readOnly: true }), 1)).toBe('readOnly');
    expect(editorKind(column('date'), '2024-01-01')).toBe('text');
  });

  it('labels the type for the header', () => {
    expect(typeLabel(column('numeric', { precision: 10, scale: 2 }))).toBe('numeric(10,2)');
    expect(typeLabel(column('text', { length: 20 }))).toBe('text(20)');
    expect(typeLabel(column('int4', { array: true }))).toBe('int4[]');
    expect(typeLabel(untyped('c'))).toBe('');
  });

  it('shows typed text as typed and other values as JSON', () => {
    expect(fieldText(new Typed('1.'))).toBe('1.');
    expect(fieldText(null)).toBe('');
    expect(fieldText({ a: 1 })).toBe('{"a":1}');
  });

  it('refuses a column type it does not know', () => {
    expect(parseColumnType({ name: 'c', kind: 'money' })).toBeNull();
    expect(parseColumnType({ kind: 'text' })).toBeNull();
    expect(parseColumnType(['c'])).toBeNull();
  });
});
