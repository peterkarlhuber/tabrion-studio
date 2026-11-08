/**
 * Untrusted strings made visible (review U-1b, U-1c of stu-005-051-review.md; threat T-08).
 *
 * Connection names, hosts and paths come from a project file that may be hostile. Rendered as they are, a newline,
 * a tab or a bidi override (U+202E) can make a list look different from what it is. `visible()` shows every C0 and
 * C1 control character, the line and paragraph separators and the bidi controls (U+061C, U+200E, U+200F,
 * U+202A..U+202E, U+2066..U+2069) as `\uXXXX`, and a backslash as `\\`, so an escape can never be faked. `capped()`
 * cuts the result to a fixed number of code points and says whether it did.
 */

/** C0, C1, U+061C, U+200E..U+200F, U+2028..U+202E (separators and bidi), U+2066..U+2069. */
const INVISIBLE: [number, number][] = [
  [0x00, 0x1f],
  [0x7f, 0x9f],
  [0x61c, 0x61c],
  [0x200e, 0x200f],
  [0x2028, 0x202e],
  [0x2066, 0x2069],
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
