/** STU-023: the SVG and PNG exports of the ER diagram are drawn in the page, escaped, and without remote references. */
import { parseDiagram, type Diagram } from '../../../api/explorer';
import { HOSTILE_TABLE, largeDiagram } from '../../../harness/explorer';
import {
  drawScene,
  fileName,
  LIGHT_PALETTE,
  pagePalette,
  pngScale,
  sceneSvg,
  xml,
  type ImageTexts,
} from './exportImage';
import { buildScene, type ErScene } from './layout';

const TEXTS: ImageTexts = {
  title: 'ER diagram of audit',
  more: (count) => `+ ${count} more`,
  line: (line) => `${line.key.name ?? '?'} -> ${line.to}`,
};

function scene(): ErScene {
  return buildScene('audit', (parseDiagram(largeDiagram(8)) as Diagram).tables);
}

describe('the SVG', () => {
  it('is a standalone document of the scene, with no style, script or outside reference', () => {
    const svg = sceneSvg(scene(), LIGHT_PALETTE, TEXTS);
    expect(svg.startsWith('<?xml version="1.0" encoding="UTF-8"?>\n<svg ')).toBe(true);
    expect(svg.match(/<g transform=/g)).toHaveLength(8);
    expect(svg.match(/marker-end="url\(#er-arrow\)"/g)).toHaveLength(8);
    expect(svg).toContain('<title>ER diagram of audit</title>');
    expect(svg).toContain('<title>audit_002_parent_fkey -&gt; audit_000</title>');
    expect(svg).toContain('+ 10 more');
    expect(svg.match(/https?:\/\/[^"]*/g)).toEqual(['http://www.w3.org/2000/svg']);
    expect(svg).not.toMatch(/href|<script|<style|\sstyle=|<image|<foreignObject|@import|on\w+=/i);
    const parsed = new DOMParser().parseFromString(svg, 'image/svg+xml');
    expect(parsed.getElementsByTagName('parsererror')).toHaveLength(0);
  });

  it('escapes names for XML after making control characters visible', () => {
    const hostile = buildScene('s', [
      {
        name: HOSTILE_TABLE,
        kind: 'table',
        columns: [{ name: '<b>&"\'', type: ']]>', nullable: true }],
        primaryKey: [],
        foreignKeys: [],
      },
    ]);
    const svg = sceneSvg(hostile, LIGHT_PALETTE, { ...TEXTS, title: '<x>' });
    expect(svg).toContain('>notes\\u202Etxt.exe\\u000Ax<');
    expect(svg).toContain('&lt;b&gt;&amp;&quot;&apos;');
    expect(svg).toContain(']]&gt;');
    expect(svg).toContain('<title>&lt;x&gt;</title>');
    const controls = [...svg].filter((char) => {
      const code = char.codePointAt(0) ?? 0;
      return (code < 0x20 && code !== 0x0a) || code === 0x202e;
    });
    expect(controls).toEqual([]);
    const parsed = new DOMParser().parseFromString(svg, 'image/svg+xml');
    expect(parsed.getElementsByTagName('parsererror')).toHaveLength(0);
    expect(xml(`a&<>"'`)).toBe('a&amp;&lt;&gt;&quot;&apos;');
  });

  it('stays a well-formed document with names XML does not allow (U+FFFE, U+FFFF, controls)', () => {
    const odd = 'a\uFFFFb\u0001c\uFFFEd\uD800e';
    const tables = buildScene(`s${odd}`, [
      {
        name: `t${odd}`,
        kind: 'table',
        columns: [{ name: `c${odd}`, type: `y${odd}`, nullable: false }],
        primaryKey: [`c${odd}`],
        foreignKeys: [
          {
            name: `k${odd}`,
            columns: [`c${odd}`],
            referredSchema: `s${odd}`,
            referredTable: `t${odd}`,
            referredColumns: [`c${odd}`],
          },
        ],
      },
    ]);
    const svg = sceneSvg(tables, LIGHT_PALETTE, { ...TEXTS, title: `ER diagram of s${odd}` });
    const parsed = new DOMParser().parseFromString(svg, 'image/svg+xml');
    expect(parsed.getElementsByTagName('parsererror')).toHaveLength(0);
    // Each refused code point is shown, never dropped: the table, its column, its type, the key and the title.
    expect(svg).toContain('>ta\\uFFFFb\\u0001c\\uFFFEd\\uD800e<');
    expect(svg).toContain('<title>ka\\uFFFFb\\u0001c\\uFFFEd\\uD800e -&gt; ta\\uFFFFb');
    expect(svg).toContain('<title>ER diagram of sa\\uFFFFb');
    expect(xml('\uFFFF\uFFFE\u0000\t\n\r😀')).toBe('\\uFFFF\\uFFFE\\u0000\t\n\r😀');
  });

  it('takes the colors of the page and nothing else', () => {
    document.documentElement.style.setProperty('--tb-color-bg', '#0e1013');
    document.documentElement.style.setProperty('--tb-color-text', 'url(https://example.invalid/x)');
    try {
      const palette = pagePalette();
      expect(palette.background).toBe('#0e1013');
      expect(palette.text).toBe(LIGHT_PALETTE.text);
    } finally {
      document.documentElement.style.removeProperty('--tb-color-bg');
      document.documentElement.style.removeProperty('--tb-color-text');
    }
  });
});

describe('file names', () => {
  it.each([
    ['public', 'svg', 'er-diagram-public.svg'],
    ['main', 'png', 'er-diagram-main.png'],
    ['../etc/passwd', 'svg', 'er-diagram-__etc_passwd.svg'],
    [HOSTILE_TABLE, 'png', 'er-diagram-notes_txt.exe_x.png'],
    ['', 'svg', 'er-diagram-schema.svg'],
    ['Ü', 'svg', 'er-diagram-_.svg'],
  ] as const)('%j as %s is %s', (schema, extension, expected) => {
    expect(fileName(schema, extension)).toBe(expected);
  });

  it('is capped', () => {
    expect(fileName('s'.repeat(300), 'svg')).toHaveLength('er-diagram-.svg'.length + 60);
  });
});

describe('the PNG', () => {
  it('is twice the size where the canvas holds it, and smaller where it would not', () => {
    expect(pngScale({ ...scene(), width: 1000, height: 800 })).toBe(2);
    const large = pngScale({ ...scene(), width: 6000, height: 4000 });
    expect(large).toBeLessThan(2);
    expect(6000 * large * 4000 * large).toBeLessThanOrEqual(16_000_000);
    const tall = pngScale({ ...scene(), width: 200, height: 30_000 });
    expect(30_000 * tall).toBeLessThanOrEqual(16_000);
  });

  it('draws every box, column and line of the scene', () => {
    const calls: string[] = [];
    const texts: string[] = [];
    const context = new Proxy(
      {},
      {
        get: (_, name: string) =>
          name === 'fillText'
            ? (text: string) => texts.push(text)
            : (...args: unknown[]) => calls.push(`${name}(${args.length})`),
        set: () => true,
      },
    ) as unknown as CanvasRenderingContext2D;
    const shown = scene();
    drawScene(context, shown, LIGHT_PALETTE, TEXTS);
    expect(texts).toContain('audit_007');
    expect(texts).toContain('+ 10 more');
    const columns = shown.boxes.reduce((sum, box) => sum + box.columns.length, 0);
    const marks = shown.boxes.reduce(
      (sum, box) => sum + box.columns.filter((column) => column.key !== null).length,
      0,
    );
    // A name per box, name and type per column, a key marker, the "more" line.
    expect(texts).toHaveLength(shown.boxes.length + columns * 2 + marks + 1);
    expect(calls.filter((call) => call === 'stroke(0)').length).toBeGreaterThanOrEqual(
      shown.lines.length + shown.boxes.length * 2,
    );
  });
});
