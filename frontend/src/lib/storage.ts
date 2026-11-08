/**
 * Per-browser preferences in localStorage (pattern: axentra-studio src/lib/storage.ts). Every access is guarded:
 * storage can be unavailable (private mode, blocked site data) and Studio must work without it. Only display
 * preferences live here; the session's CSRF value is in sessionStorage (src/session.ts), never in localStorage.
 */
const PREFIX = 'tabrion-studio.';

export type PreferenceKey = 'locale' | 'theme' | 'density';

export function readPreference(key: PreferenceKey): string | null {
  try {
    return window.localStorage.getItem(PREFIX + key);
  } catch {
    return null;
  }
}

export function writePreference(key: PreferenceKey, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(PREFIX + key);
    else window.localStorage.setItem(PREFIX + key, value);
  } catch {
    // Storage unavailable: the preference simply does not persist.
  }
}
