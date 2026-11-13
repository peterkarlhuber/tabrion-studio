/**
 * Untrusted strings made visible (review U-1b, U-1c of stu-005-051-review.md; threat T-08).
 *
 * Connection names, hosts and paths come from a project file that may be hostile. Rendered as they are, a newline,
 * a tab or a bidi override (U+202E) can make a list look different from what it is. `visible()` shows every C0 and
 * C1 control character, the line and paragraph separators, the bidi controls (U+061C, U+200E, U+200F,
 * U+202A..U+202E, U+2066..U+2069), the zero-width characters (U+200B..U+200D, U+2060..U+2064, U+FEFF) and the
 * characters that show as a blank (U+034F, the Hangul fillers U+115F, U+1160, U+3164, U+FFA0, the Khmer inherent
 * vowels U+17B4, U+17B5, the braille blank U+2800) as `\uXXXX`, and a backslash as `\\`, so an escape can never be
 * faked and two values never look alike. `capped()`
 * cuts the result to a fixed number of code points and says whether it did.
 */

/** C0, C1, bidi controls, separators, zero-width and blank-looking characters (see above). */
const INVISIBLE: [number, number][] = [
  [0x00, 0x1f],
  [0x7f, 0x9f],
  [0x34f, 0x34f],
  [0x61c, 0x61c],
  [0x115f, 0x1160],
  [0x17b4, 0x17b5],
  [0x200b, 0x200f],
  [0x2028, 0x202e],
  [0x2060, 0x2064],
  [0x2066, 0x2069],
  [0x2800, 0x2800],
  [0x3164, 0x3164],
  [0xfeff, 0xfeff],
  [0xffa0, 0xffa0],
];
const BACKSLASH = 0x5c;

function token(char: string): string {
  const code = char.codePointAt(0) ?? 0;
  if (code === BACKSLASH) return '\\\\';
  if (!INVISIBLE.some(([low, high]) => code >= low && code <= high)) return char;
  return `\\u${code.toString(16).toUpperCase().padStart(4, '0')}`;
}

/** Every character that could change how a value reads, as a visible escape. */
export function visible(value: string): string {
  return Array.from(value, token).join('');
}

/** The longest entry shown in full; a longer one is cut with a visible marker (U-1c). */
export const MAX_ENTRY_LENGTH = 64;
/** How many entries of a list are shown before "and N more" (U-1c). */
export const MAX_ENTRIES = 8;

/** `visible(value)` cut to about `limit` characters: `[shown, cut]`. Never splits a surrogate pair or an escape. */
export function capped(value: string, limit = MAX_ENTRY_LENGTH): [string, boolean] {
  let shown = '';
  let length = 0;
  for (const char of value) {
    const part = token(char);
    if (length + part.length > limit) return [shown, true];
    shown += part;
    length += part.length;
  }
  return [shown, false];
}
