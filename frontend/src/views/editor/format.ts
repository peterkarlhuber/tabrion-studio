/**
 * Cell values as text (08 section 4.4; threat T-07: database content is never rendered as HTML).
 *
 * The server sends JSON values: numbers beyond JavaScript's safe integers, decimals, dates, times and UUIDs as text,
 * bytes as `{"base64": ...}`, JSON as JSON (docs/architecture/queries.md). The grid shows one line per cell: line
 * breaks, tabs and the characters that could make a value read differently (other control characters, bidi
 * controls, line and paragraph separators) are shown as visible marks. The detail panel shows the whole value as
 * plain text, JSON indented and bytes as hex.
 */

export type CellKind = 'null' | 'text' | 'number' | 'boolean' | 'json' | 'bytes';

/** How many characters a grid cell shows; the detail panel shows the rest. */
export const PREVIEW_CHARS = 200;
/** How many bytes the hex view shows (the server cuts bytes at 32 KB anyway). */
export const HEX_BYTES = 32 * 1024;

export function cellKind(value: unknown): CellKind {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'string') return 'text';
  if (typeof value === 'number') return 'number';
  if (typeof value === 'boolean') return 'boolean';
  if (isBytes(value)) return 'bytes';
  return 'json';
}

export function isBytes(value: unknown): value is { base64: string } {
  return (
    typeof value === 'object' &&
    value !== null &&
    !Array.isArray(value) &&
    Object.keys(value).length === 1 &&
    typeof (value as { base64?: unknown }).base64 === 'string'
  );
}

const MARKS: Record<string, string> = { '\n': '↵', '\r': '␍', '\t': '⇥' };
// C0 and C1 controls, U+061C, U+200E/F, U+2028..U+202E, U+2066..U+2069 (the set of lib/visible.ts).
// eslint-disable-next-line no-control-regex
const INVISIBLE = /[\u0000-\u001f\u007f-\u009f\u061c\u200e\u200f\u2028-\u202e\u2066-\u2069]/g;

/** One line of text with every invisible or line-breaking character shown as a mark. */
export function oneLine(text: string): string {
  return text.replace(
    INVISIBLE,
    (char) =>
      MARKS[char] ??
      `⟨U+${(char.codePointAt(0) ?? 0).toString(16).toUpperCase().padStart(4, '0')}⟩`,
  );
}

/** Text for the detail panel: line breaks and tabs stay, every other invisible character is shown as a mark. */
export function markInvisible(text: string): string {
  return text.replace(INVISIBLE, (char) =>
    char === '\n' || char === '\t' || char === '\r' ? char : oneLine(char),
  );
}

function bytesOf(base64: string): Uint8Array {
  try {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return bytes;
  } catch {
    return new Uint8Array();
  }
}

function hexByte(byte: number): string {
  return byte.toString(16).padStart(2, '0');
}

/** The full value as text for the detail panel and for copying. */
export function fullText(value: unknown): string {
  switch (cellKind(value)) {
    case 'null':
      return 'NULL';
    case 'text':
      return value as string;
    case 'number':
    case 'boolean':
      return String(value);
    case 'bytes':
      return hexDump((value as { base64: string }).base64);
    default:
      return JSON.stringify(value, null, 2);
  }
}

/** The grid's text: one line, at most PREVIEW_CHARS characters (plus "…" when cut here). */
export function previewText(value: unknown): string {
  let text: string;
  switch (cellKind(value)) {
    case 'null':
      return 'NULL';
    case 'text':
      text = value as string;
      break;
    case 'number':
    case 'boolean':
      return String(value);
    case 'bytes': {
      const bytes = bytesOf((value as { base64: string }).base64);
      text = `\\x${Array.from(bytes.subarray(0, PREVIEW_CHARS / 2), hexByte).join('')}`;
      if (bytes.length > PREVIEW_CHARS / 2) text += '…';
      return text;
    }
    default:
      text = JSON.stringify(value);
  }
  const chars = Array.from(text);
  const shown = chars.length > PREVIEW_CHARS ? `${chars.slice(0, PREVIEW_CHARS).join('')}…` : text;
  return oneLine(shown);
}

/** Bytes as a hex dump: offset, 16 bytes in hex, the printable ASCII characters. */
export function hexDump(base64: string): string {
  const bytes = bytesOf(base64).subarray(0, HEX_BYTES);
  const lines: string[] = [];
  for (let offset = 0; offset < bytes.length; offset += 16) {
    const chunk = Array.from(bytes.subarray(offset, offset + 16));
    const hex = chunk.map(hexByte).join(' ').padEnd(47, ' ');
    const ascii = chunk.map((b) => (b >= 0x20 && b < 0x7f ? String.fromCharCode(b) : '.')).join('');
    lines.push(`${offset.toString(16).padStart(8, '0')}  ${hex}  ${ascii}`);
  }
  return lines.join('\n');
}

/** The byte count of a bytes value. */
export function byteLength(value: { base64: string }): number {
  return bytesOf(value.base64).length;
}

/** "1 234" style counts in the page's language. */
export function formatCount(count: number, locale: string): string {
  return new Intl.NumberFormat(locale).format(count);
}

/** A duration in milliseconds: one decimal below 10 ms ("0.4", "2.5"), whole milliseconds above ("24", "1,234"). */
export function formatMs(ms: number, locale: string): string {
  return new Intl.NumberFormat(locale, { maximumFractionDigits: ms < 10 ? 1 : 0 }).format(ms);
}

/** A duration in seconds with one decimal ("4.2", "4,2" in German). */
export function formatSeconds(ms: number, locale: string): string {
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(ms / 1000);
}
