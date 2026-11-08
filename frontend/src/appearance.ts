/**
 * Language, theme and density in Studio's own `settings.toml` (TD-021, 08 section 4.10). Studio listens on a free
 * port, and a browser keeps localStorage per origin (port included), so preferences kept only in the browser are
 * lost at the next start. The page therefore saves every choice to `settings.toml` as well (`PUT /api/settings` with
 * only that key; keys left out keep their value) and applies the file's choices when it opens.
 *
 * - The file wins at the start: a value that differs from the default replaces the browser's. A default value
 *   ("system", "compact") leaves the browser's own preference alone.
 * - A file with issues or one written by hand is never changed from here (that is the settings form's decision, with
 *   its warning): the choice then stays in this browser and the settings view says so.
 * - Saves run one after the other, each with a fresh digest; a stale digest is retried once.
 */
import { useSyncExternalStore } from 'react';

import { readProblem } from './api/problem';
import { isLocale, setLocale, type Locale } from './i18n';
import { apiHeaders } from './session';
import { setDensity, setThemePreference, type Density, type ThemePreference } from './theme';

export interface Appearance {
  language: 'system' | Locale;
  theme: ThemePreference;
  density: Density;
}

export const DEFAULT_APPEARANCE: Appearance = {
  language: 'system',
  theme: 'system',
  density: 'compact',
};

/** Where the last choice went: the file, this browser only (and why), or nowhere yet. */
export type SyncState =
  | { kind: 'idle' }
  | { kind: 'saved' }
  | { kind: 'local'; reason: 'issues' | 'hand_written' | 'unavailable' };

interface FileInfo {
  appearance: Appearance;
  digest: string | null;
  writable: 'yes' | 'issues' | 'hand_written';
}

let fetcher: typeof fetch | null = null;
let state: SyncState = { kind: 'idle' };
/** How many choices reached the file: the settings form reads the new digest when it grows. */
let saves = 0;
let queue: Promise<unknown> = Promise.resolve();
const listeners = new Set<() => void>();

function setState(next: SyncState): void {
  state = next;
  if (next.kind === 'saved') saves += 1;
  for (const listener of listeners) listener();
}

/** The appearance in a `GET /api/settings` body, or null for anything else. Unknown values are the defaults. */
export function parseAppearance(body: unknown): Appearance | null {
  if (typeof body !== 'object' || body === null) return null;
  const settings = (body as Record<string, unknown>).settings;
  if (typeof settings !== 'object' || settings === null) return null;
  const raw = settings as Record<string, unknown>;
  return {
    language: isLocale(raw.language) ? raw.language : 'system',
    theme: raw.theme === 'light' || raw.theme === 'dark' ? raw.theme : 'system',
    density: raw.density === 'normal' ? 'normal' : 'compact',
  };
}

async function readFile(using: typeof fetch): Promise<FileInfo | null> {
  try {
    const response = await using('/api/settings', {
      cache: 'no-store',
      credentials: 'same-origin',
      headers: apiHeaders(),
    });
    if (!response.ok) return null;
    const body: unknown = await response.json();
    const appearance = parseAppearance(body);
    if (appearance === null) return null;
    const doc = body as Record<string, unknown>;
    const file = (doc.file ?? {}) as Record<string, unknown>;
    const issues = Array.isArray(doc.issues) ? (doc.issues as { severity?: unknown }[]) : [];
    const writable = issues.some((issue) => issue.severity !== 'warning')
      ? 'issues'
      : file.hand_written === true
        ? 'hand_written'
        : 'yes';
    return {
      appearance,
      digest: typeof file.digest === 'string' ? file.digest : null,
      writable,
    };
  } catch {
    return null;
  }
}

/** Apply the file's choices that differ from the defaults (see the module comment). */
export function applyAppearance(appearance: Appearance): void {
  if (appearance.language !== 'system') void setLocale(appearance.language);
  if (appearance.theme !== DEFAULT_APPEARANCE.theme) setThemePreference(appearance.theme);
  if (appearance.density !== DEFAULT_APPEARANCE.density) setDensity(appearance.density);
}

/**
 * Start following `settings.toml` with this fetcher: read it once and apply its choices. Returns the stop function.
 * Without a session (tests, the sign-in notice) nothing is started.
 */
export function startAppearanceSync(using: typeof fetch): () => void {
  fetcher = using;
  let active = true;
  void readFile(using).then((info) => {
    if (active && info !== null) applyAppearance(info.appearance);
  });
  return () => {
    active = false;
    if (fetcher === using) fetcher = null;
  };
}

async function save(patch: Partial<Appearance>): Promise<SyncState> {
  const using = fetcher;
  if (using === null) return { kind: 'local', reason: 'unavailable' };
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const info = await readFile(using);
    if (info === null) return { kind: 'local', reason: 'unavailable' };
    if (info.writable !== 'yes') return { kind: 'local', reason: info.writable };
    try {
      const response = await using('/api/settings', {
        method: 'PUT',
        cache: 'no-store',
        credentials: 'same-origin',
        headers: { ...apiHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings: patch, digest: info.digest }),
      });
      if (response.ok) return { kind: 'saved' };
      const problem = await readProblem(response);
      if (problem.code === 'studio.config_stale') continue; // changed in between: read it again
      if (problem.code === 'studio.config_hand_written')
        return { kind: 'local', reason: 'hand_written' };
      return { kind: 'local', reason: 'unavailable' };
    } catch {
      return { kind: 'local', reason: 'unavailable' };
    }
  }
  return { kind: 'local', reason: 'unavailable' };
}

/** Save one choice to `settings.toml`, after the saves before it. The choice itself is applied by the caller. */
export function rememberAppearance(patch: Partial<Appearance>): Promise<SyncState> {
  const next = queue.then(() => save(patch));
  queue = next.catch(() => undefined);
  void next.then(setState);
  return next;
}

export function chooseLanguage(language: Locale): void {
  void setLocale(language);
  void rememberAppearance({ language });
}

export function chooseTheme(theme: ThemePreference): void {
  setThemePreference(theme);
  void rememberAppearance({ theme });
}

export function chooseDensity(density: Density): void {
  setDensity(density);
  void rememberAppearance({ density });
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Where the last choice was saved (for the settings view). */
export function useAppearanceSync(): SyncState {
  return useSyncExternalStore(subscribe, () => state);
}

/** The number of choices saved to the file so far. */
export function useAppearanceSaves(): number {
  return useSyncExternalStore(subscribe, () => saves);
}

/** Tests: forget the fetcher, the state and the queue. */
export function resetAppearanceSync(): void {
  fetcher = null;
  queue = Promise.resolve();
  setState({ kind: 'idle' });
}
