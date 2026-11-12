import { describe, expect, it } from 'vitest';

import { cellKind, fullText, hexDump, markInvisible, previewText } from './format';

describe('cell values', () => {
  it('knows the kinds the server sends', () => {
    expect([null, 'a', 1, true, { a: 1 }, [1], { base64: 'AA==' }].map(cellKind)).toEqual([
      'null',
      'text',
      'number',
      'boolean',
      'json',
      'json',
      'bytes',
    ]);
  });

  it('shows one line in the grid: breaks, tabs and bidi controls as marks, cut at 200 characters', () => {
    expect(previewText('a\nb\tc‮d')).toBe('a↵b⇥c⟨U+202E⟩d');
    expect(previewText('x'.repeat(300))).toBe(`${'x'.repeat(200)}…`);
    expect(previewText(null)).toBe('NULL');
    expect(previewText({ base64: btoa('Þ­') })).toBe('\\xdead');
    expect(previewText({ a: '<b>' })).toBe('{"a":"<b>"}');
  });

  it('shows the whole value in the detail panel: JSON indented, bytes as hex, controls marked', () => {
    expect(fullText({ a: 1 })).toBe('{\n  "a": 1\n}');
    expect(hexDump(btoa('AB\u0000'))).toBe(`00000000  41 42 00${' '.repeat(39)}  AB.`);
    expect(markInvisible('a\nb‮')).toBe('a\nb⟨U+202E⟩');
  });
});
