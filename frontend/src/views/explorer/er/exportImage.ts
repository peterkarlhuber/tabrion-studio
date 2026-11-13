/**
 * The ER diagram as a file (STU-023, 03 section 2 E "Export ER als SVG/PNG", 08 section 8 "PNG 2×"): both drawn in the
 * page from the scene (`layout.ts`), never from a server and never with a remote resource (no font, image or style
 * sheet is fetched; the SVG names local font families only).
 *
 * - **SVG:** a standalone document with presentation attributes (no `style`, no script, no external reference);
 *   every text of the database escaped for XML after `visible()` (no control character can reach the file).
 * - **PNG:** the same drawing on a canvas through the 2D API, at twice the size where the browser can hold it. It does
 *   not go through an `<img>` of the SVG: the CSP of STU-003 (`img-src 'self'`) refuses `blob:` and `data:` images.
 *
 * The colors are the page's tokens (`--tb-color-*`) at the time of the export, so a dark page saves a dark diagram.
 */
import { GEOMETRY, type ErBox, type ErLine, type ErScene, type Point } from './layout';

export interface Palette {
  background: string;
  surface: string;
  header: string;
  border: string;
  text: string;
  muted: string;
  line: string;
  key: string;
}

/** The light theme's tokens: used when the page's are not readable (a test without the style sheet). */
export const LIGHT_PALETTE: Palette = {
  background: '#f4f5f7',
  surface: '#ffffff',
  header: '#e8eaf6',
  border: '#c3c7cf',
  text: '#16181d',
  muted: '#4b505b',
  line: '#5d626e',
  key: '#3949ab',
};

const TOKENS: Record<keyof Palette, string> = {
  background: '--tb-color-bg',
  surface: '--tb-color-surface',
  header: '--tb-color-primary-subtle',
  border: '--tb-color-border-strong',
  text: '--tb-color-text',
  muted: '--tb-color-text-muted',
  line: '--tb-color-text-subtle',
  key: '--tb-color-primary',
};

/** Only a plain color reaches the file (a token is page text, but nothing else belongs in an attribute). */
const COLOR = /^(#[0-9a-f]{3,8}|rgba?\([0-9., %]+\))$/i;

export function pagePalette(root: Element = document.documentElement): Palette {
  const style = getComputedStyle(root);
  const palette = { ...LIGHT_PALETTE };
  for (const key of Object.keys(TOKENS) as (keyof Palette)[]) {
    const value = style.getPropertyValue(TOKENS[key]).trim();
    if (COLOR.test(value)) palette[key] = value;
  }
  return palette;
}

export const FONT_FAMILY =
  "ui-monospace, 'SFMono-Regular', Menlo, Consolas, 'Liberation Mono', monospace";
const FONT_SIZE = 12;
const ARROW = 9;

/** Whether a code point is a `Char` of XML 1.0 (section 2.2): not a control but tab, newline and return; no surrogate; not U+FFFE or U+FFFF. */
function xmlChar(code: number): boolean {
  return (
    code === 0x9 ||
    code === 0xa ||
    code === 0xd ||
    (code >= 0x20 && code <= 0xd7ff) ||
    (code >= 0xe000 && code <= 0xfffd) ||
    (code >= 0x10000 && code <= 0x10ffff)
  );
}

/**
 * Text for XML content and attribute values. A code point XML 1.0 does not allow (U+FFFE, U+FFFF, a lone surrogate, a
 * control that `visible()` did not already show) becomes a visible `\uXXXX`, so the document always parses.
 */
export function xml(value: string): string {
  return Array.from(value, (char) => {
    const code = char.codePointAt(0) ?? 0;
    return xmlChar(code) ? char : `\\u${code.toString(16).toUpperCase().padStart(4, '0')}`;
  })
    .join('')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

/** A file name part of the schema: letters, digits, `-`, `_` and `.`; anything else becomes `_`. */
export function fileName(schema: string, extension: 'svg' | 'png'): string {
  const safe =
    Array.from(schema)
      .slice(0, 60)
      .map((char) => (/^[A-Za-z0-9._-]$/.test(char) ? char : '_'))
      .join('')
      .replace(/^\.+/, '_') || 'schema';
  return `er-diagram-${safe}.${extension}`;
}

function n(value: number): string {
  return String(Math.round(value * 10) / 10);
}

function path(points: Point[]): string {
  return points
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${n(point.x)} ${n(point.y)}`)
    .join(' ');
}

function keyText(mark: ErBox['columns'][number]['key']): string {
  if (mark === 'pkfk') return 'PF';
  if (mark === 'pk') return 'PK';
  return mark === 'fk' ? 'FK' : '';
}

/** Texts the image needs in the page's language. */
export interface ImageTexts {
  title: string;
  more: (count: number) => string;
  /** The tooltip of a line: the key's name and columns. */
  line: (line: ErLine) => string;
}

function svgBox(item: ErBox, palette: Palette, texts: ImageTexts): string {
  const g = GEOMETRY;
  const parts = [
    `<g transform="translate(${n(item.x)} ${n(item.y)})">`,
    `<rect width="${item.width}" height="${item.height}" rx="6" fill="${palette.surface}" stroke="${palette.border}"/>`,
    `<path d="M0 6 A6 6 0 0 1 6 0 H${item.width - 6} A6 6 0 0 1 ${item.width} 6 V${g.header} H0 Z" fill="${palette.header}"/>`,
    `<line x1="0" y1="${g.header}" x2="${item.width}" y2="${g.header}" stroke="${palette.border}"/>`,
    `<text x="${g.padX}" y="${g.header / 2 + 4}" font-weight="700" fill="${palette.text}">${xml(item.label)}</text>`,
  ];
  item.columns.forEach((column, index) => {
    const y = g.header + g.padY + index * g.row + g.row / 2 + 4;
    const mark = keyText(column.key);
    if (mark !== '') {
      parts.push(
        `<text x="${g.padX}" y="${y}" font-size="10" font-weight="700" fill="${palette.key}">${mark}</text>`,
      );
    }
    parts.push(
      `<text x="${g.padX + g.badge}" y="${y}" fill="${palette.text}">${xml(column.name)}</text>`,
      `<text x="${item.width - g.padX}" y="${y}" text-anchor="end" fill="${palette.muted}">${xml(column.type)}</text>`,
    );
  });
  if (item.hidden > 0) {
    const y = g.header + g.padY + item.columns.length * g.row + g.row / 2 + 4;
    parts.push(
      `<text x="${g.padX}" y="${y}" font-style="italic" fill="${palette.muted}">${xml(texts.more(item.hidden))}</text>`,
    );
  }
  parts.push('</g>');
  return parts.join('');
}

/** The scene as a standalone SVG document. */
export function sceneSvg(scene: ErScene, palette: Palette, texts: ImageTexts): string {
  const { width, height } = scene;
  const lines = scene.lines
    .filter((line) => line.points.length > 1)
    .map(
      (line) =>
        `<path d="${path(line.points)}" fill="none" stroke="${palette.line}" stroke-width="1.5" marker-end="url(#er-arrow)"><title>${xml(texts.line(line))}</title></path>`,
    );
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" font-family="${xml(FONT_FAMILY)}" font-size="${FONT_SIZE}">`,
    `<title>${xml(texts.title)}</title>`,
    `<defs><marker id="er-arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="${ARROW}" markerHeight="${ARROW}" markerUnits="userSpaceOnUse" orient="auto"><path d="M0 0 L10 5 L0 10 Z" fill="${palette.line}"/></marker></defs>`,
    `<rect width="${width}" height="${height}" fill="${palette.background}"/>`,
    ...lines,
    ...scene.boxes.map((item) => svgBox(item, palette, texts)),
    '</svg>',
    '',
  ].join('\n');
}

/** The largest canvas every supported browser draws (WebKit: 16.7 million pixels; 16 384 per side). */
const MAX_PIXELS = 16_000_000;
const MAX_SIDE = 16_000;

/** Twice the size (08 section 8, "Retina-Schärfe"), less where the canvas would be too large. */
export function pngScale(scene: ErScene): number {
  return Math.min(
    2,
    Math.sqrt(MAX_PIXELS / Math.max(1, scene.width * scene.height)),
    MAX_SIDE / Math.max(1, scene.width),
    MAX_SIDE / Math.max(1, scene.height),
  );
}

function roundedRect(context: CanvasRenderingContext2D, item: ErBox): void {
  const r = 6;
  context.beginPath();
  context.moveTo(item.x + r, item.y);
  context.arcTo(item.x + item.width, item.y, item.x + item.width, item.y + item.height, r);
  context.arcTo(item.x + item.width, item.y + item.height, item.x, item.y + item.height, r);
  context.arcTo(item.x, item.y + item.height, item.x, item.y, r);
  context.arcTo(item.x, item.y, item.x + item.width, item.y, r);
  context.closePath();
}

/** Draw the scene on `context` (already scaled). */
export function drawScene(
  context: CanvasRenderingContext2D,
  scene: ErScene,
  palette: Palette,
  texts: ImageTexts,
): void {
  const g = GEOMETRY;
  context.fillStyle = palette.background;
  context.fillRect(0, 0, scene.width, scene.height);
  context.strokeStyle = palette.line;
  context.fillStyle = palette.line;
  context.lineWidth = 1.5;
  for (const line of scene.lines) {
    if (line.points.length < 2) continue;
    context.beginPath();
    line.points.forEach((point, index) =>
      index === 0 ? context.moveTo(point.x, point.y) : context.lineTo(point.x, point.y),
    );
    context.stroke();
    const end = line.points[line.points.length - 1];
    const before = line.points[line.points.length - 2];
    if (end === undefined || before === undefined) continue;
    const angle = Math.atan2(end.y - before.y, end.x - before.x);
    context.beginPath();
    context.moveTo(end.x, end.y);
    context.lineTo(end.x - ARROW * Math.cos(angle - 0.45), end.y - ARROW * Math.sin(angle - 0.45));
    context.lineTo(end.x - ARROW * Math.cos(angle + 0.45), end.y - ARROW * Math.sin(angle + 0.45));
    context.closePath();
    context.fill();
  }
  context.lineWidth = 1;
  context.textBaseline = 'middle';
  for (const item of scene.boxes) {
    roundedRect(context, item);
    context.fillStyle = palette.surface;
    context.fill();
    context.save();
    context.clip();
    context.fillStyle = palette.header;
    context.fillRect(item.x, item.y, item.width, g.header);
    context.restore();
    context.strokeStyle = palette.border;
    roundedRect(context, item);
    context.stroke();
    context.beginPath();
    context.moveTo(item.x, item.y + g.header);
    context.lineTo(item.x + item.width, item.y + g.header);
    context.stroke();
    context.textAlign = 'left';
    context.font = `700 ${FONT_SIZE}px ${FONT_FAMILY}`;
    context.fillStyle = palette.text;
    context.fillText(item.label, item.x + g.padX, item.y + g.header / 2);
    item.columns.forEach((column, index) => {
      const y = item.y + g.header + g.padY + index * g.row + g.row / 2;
      const mark = keyText(column.key);
      if (mark !== '') {
        context.textAlign = 'left';
        context.font = `700 10px ${FONT_FAMILY}`;
        context.fillStyle = palette.key;
        context.fillText(mark, item.x + g.padX, y);
      }
      context.font = `${FONT_SIZE}px ${FONT_FAMILY}`;
      context.textAlign = 'left';
      context.fillStyle = palette.text;
      context.fillText(column.name, item.x + g.padX + g.badge, y);
      context.textAlign = 'right';
      context.fillStyle = palette.muted;
      context.fillText(column.type, item.x + item.width - g.padX, y);
    });
    if (item.hidden > 0) {
      const y = item.y + g.header + g.padY + item.columns.length * g.row + g.row / 2;
      context.textAlign = 'left';
      context.font = `italic ${FONT_SIZE}px ${FONT_FAMILY}`;
      context.fillStyle = palette.muted;
      context.fillText(texts.more(item.hidden), item.x + g.padX, y);
    }
  }
}

/** The scene as a PNG, or null where the browser cannot draw or encode the canvas. */
export async function scenePng(
  scene: ErScene,
  palette: Palette,
  texts: ImageTexts,
): Promise<Blob | null> {
  const scale = pngScale(scene);
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.floor(scene.width * scale));
  canvas.height = Math.max(1, Math.floor(scene.height * scale));
  let context: CanvasRenderingContext2D | null;
  try {
    context = canvas.getContext('2d');
  } catch {
    return null;
  }
  if (context === null) return null;
  context.scale(scale, scale);
  drawScene(context, scene, palette, texts);
  return new Promise((resolve) => {
    try {
      canvas.toBlob((blob) => resolve(blob), 'image/png');
    } catch {
      resolve(null);
    }
  });
}
