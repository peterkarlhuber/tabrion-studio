/**
 * `.sql` scripts in the project folder (STU-020, 03 section 2 C; backend: docs/architecture/history-and-scripts.md).
 *
 * - `GET /api/scripts`: the scripts of the project, by path relative to its folder (with `/`).
 * - `GET /api/scripts/file?path=`: a script's text and the digest of its bytes.
 * - `PUT /api/scripts/file` with `{path, content, digest, overwrite?}`: `digest` is what the page read (`null` for a
 *   new file); a file that changed or appeared meanwhile is `studio.script_stale` or `studio.script_exists`, and the
 *   page asks before it sends `overwrite: true`.
 */
import { read } from './explorer';
import { send, type Result } from './profiles';

export interface ScriptItem {
  path: string;
  size: number;
}

export interface ScriptList {
  scripts: ScriptItem[];
  truncated: boolean;
  /** Links and reparse-point folders (OneDrive placeholders) the listing left out. */
  skippedLinks: number;
  /** Folders and `.sql` files left out because Studio refuses their name. */
  skippedInvalid: number;
}

export interface ScriptFile {
  path: string;
  content: string;
  digest: string;
}

function count(value: unknown): number {
  return typeof value === 'number' && Number.isInteger(value) ? Math.max(0, value) : 0;
}

export function parseScriptList(body: unknown): ScriptList | null {
  if (typeof body !== 'object' || body === null) return null;
  const doc = body as Record<string, unknown>;
  if (!Array.isArray(doc.scripts)) return null;
  return {
    scripts: doc.scripts.flatMap((item: unknown) => {
      const value = (item ?? {}) as { path?: unknown; size?: unknown };
      return typeof value.path === 'string'
        ? [{ path: value.path, size: typeof value.size === 'number' ? value.size : 0 }]
        : [];
    }),
    truncated: doc.truncated === true,
    skippedLinks: count(doc.skipped_links),
    skippedInvalid: count(doc.skipped_invalid),
  };
}

export function parseScriptFile(body: unknown): ScriptFile | null {
  if (typeof body !== 'object' || body === null) return null;
  const doc = body as Record<string, unknown>;
  return typeof doc.path === 'string' &&
    typeof doc.content === 'string' &&
    typeof doc.digest === 'string'
    ? { path: doc.path, content: doc.content, digest: doc.digest }
    : null;
}

export function fetchScripts(fetcher: typeof fetch): Promise<Result<ScriptList>> {
  return read(fetcher, '/api/scripts', parseScriptList);
}

export function readScript(fetcher: typeof fetch, path: string): Promise<Result<ScriptFile>> {
  return read(fetcher, `/api/scripts/file?path=${encodeURIComponent(path)}`, (body) => {
    const file = parseScriptFile(body);
    // The answer must be the script asked for.
    return file !== null && file.path === path ? file : null;
  });
}

export function saveScript(
  fetcher: typeof fetch,
  script: { path: string; content: string; digest: string | null; overwrite?: boolean },
): Promise<Result<{ path: string; digest: string }>> {
  return send(fetcher, 'PUT', '/api/scripts/file', {
    path: script.path,
    content: script.content,
    digest: script.digest,
    overwrite: script.overwrite ?? false,
  });
}

/** The last name of a script path, without `.sql` (a tab's title). */
export function scriptTitle(path: string): string {
  const name = path.split('/').pop() ?? path;
  return name.replace(/\.sql$/i, '') || name;
}

/** Windows device names, also with an extension (`nul.sql`); refused on every system (the server does the same). */
const DEVICE =
  /^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[0-9\u00b9\u00b2\u00b3]|LPT[0-9\u00b9\u00b2\u00b3])$/i;

/**
 * Characters that look blank: every space but U+0020 and the ideographic space U+3000, the Hangul fillers, the
 * braille blank, U+034F, U+17B4/5.
 */
const BLANK = /(?![ \u3000])\p{Zs}|\u034f|\u115f|\u1160|\u17b4|\u17b5|\u2800|\u3164|\uffa0/u;

/**
 * A zero-width non-joiner or joiner in its place (E-1 of the STU-020 delta review): between two letters or marks
 * (Persian, Indic conjuncts), a ZWJ also inside an emoji sequence (after a symbol, a skin tone or a variation
 * selector, before a symbol). Never at the start or end, never doubled, never next to a space, a dot or a digit.
 * The lookbehind and lookahead see the neighbour code points, so `u` handles astral emoji.
 */
const JOINED =
  /(?<=[\p{L}\p{M}])[\u200c\u200d](?=[\p{L}\p{M}])|(?<=[\p{So}\p{Sk}\p{Mn}])\u200d(?=\p{So})/gu;

/** Whether one name of a script path is a plain name (the server's rules, `services/scripts.py`). */
function plainName(part: string): boolean {
  // `trim()` also removes U+3000, so the ideographic space is never leading or trailing, like U+0020.
  if (part === '' || part.startsWith('.') || part.endsWith('.') || part !== part.trim())
    return false;
  // An 8.3 short name (GIT~1), a format character (bidi, zero-width; a joiner only in its place), a blank.
  const rest = part.replace(JOINED, '');
  if (/~[0-9]/.test(part) || /\p{Cf}/u.test(rest) || BLANK.test(part)) return false;
  return !DEVICE.test((part.split('.')[0] ?? '').replace(/ +$/, ''));
}

/** Whether `path` has the form the server accepts (checked again there): relative plain names ending in `.sql`. */
export function validScriptPath(path: string): boolean {
  if (path.length === 0 || path.length > 512 || path.startsWith('/')) return false;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f-\u009f\u2028\u2029\\:*?"<>|]/.test(path)) return false;
  const parts = path.split('/');
  if (!parts.every(plainName)) return false;
  const last = parts[parts.length - 1] ?? '';
  return last.length > 4 && last.toLowerCase().endsWith('.sql');
}
