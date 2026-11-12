/**
 * Keyboard shortcuts (08 section 7, STU-052). One table for matching and for display, so that the command palette,
 * the help dialog and the header buttons always show the shortcut that really works on this platform: `mod` is ⌘ on
 * macOS and Ctrl elsewhere (08 section 10). Browser-reserved shortcuts (⌘W, ⌘T, ⌘N) are never taken.
 */

export type Platform = 'mac' | 'other';

export interface Shortcut {
  /** `KeyboardEvent.key`, lower case for letters (`k`, `/`, `F6`). */
  key: string;
  /** ⌘ on macOS, Ctrl elsewhere. */
  mod?: boolean;
  shift?: boolean;
  alt?: boolean;
}

/**
 * The shortcuts that work now. The SQL editor's (STU-012) work on its page: a new editor tab is Alt+T and closing one
 * Alt+W (⌥T and ⌥W on a Mac), because the browser keeps ⌘T and ⌘W and Safari's ⌥⌘W closes the other tabs (08
 * section 7). The explorer filter (STU-010) works on the explorer page only.
 */
export const SHORTCUTS = {
  palette: { key: 'k', mod: true },
  help: { key: '/', mod: true },
  nextRegion: { key: 'F6' },
  previousRegion: { key: 'F6', shift: true },
  run: { key: 'Enter', mod: true },
  runScript: { key: 'Enter', mod: true, shift: true },
  cancel: { key: '.', mod: true },
  newTab: { key: 't', alt: true },
  closeTab: { key: 'w', alt: true },
  save: { key: 's', mod: true },
  /** On the explorer page (STU-010): the object filter. The browser's print dialog stays on every other page. */
  explorerFilter: { key: 'p', mod: true },
} as const satisfies Record<string, Shortcut>;

/**
 * The shortcuts of the whole page, those of the SQL editor's page and those of the explorer's (the help lists them in
 * three groups).
 */
export const PAGE_SHORTCUTS: ShortcutId[] = ['palette', 'help', 'nextRegion', 'previousRegion'];
export const EDITOR_SHORTCUTS: ShortcutId[] = [
  'run',
  'runScript',
  'cancel',
  'newTab',
  'closeTab',
  'save',
];
export const EXPLORER_SHORTCUTS: ShortcutId[] = ['explorerFilter'];

export type ShortcutId = keyof typeof SHORTCUTS;

interface NavigatorLike {
  platform?: string;
  userAgent?: string;
  userAgentData?: { platform?: string };
}

export function detectPlatform(nav: NavigatorLike | undefined = globalThis.navigator): Platform {
  if (nav === undefined) return 'other';
  const name = nav.userAgentData?.platform ?? nav.platform ?? nav.userAgent ?? '';
  return /mac|iphone|ipad|ipod/i.test(name) ? 'mac' : 'other';
}

/** Punctuation is reached with Shift on many layouts (`/` is Shift+7 on a German keyboard): Shift is ignored then. */
function shiftMatters(key: string): boolean {
  return /^[a-z0-9]$/i.test(key) || /^F\d{1,2}$/.test(key) || key.length > 1;
}

export function matches(
  event: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'shiftKey' | 'altKey'> & {
    code?: string;
  },
  shortcut: Shortcut,
  platform: Platform,
): boolean {
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  // With ⌥ on macOS a letter key gives another character (⌥T is "†"): the physical key decides then.
  const physical =
    shortcut.alt === true &&
    /^[a-z]$/.test(shortcut.key) &&
    event.code === `Key${shortcut.key.toUpperCase()}`;
  if (key !== shortcut.key && !physical) return false;
  const mod = platform === 'mac' ? event.metaKey : event.ctrlKey;
  const other = platform === 'mac' ? event.ctrlKey : event.metaKey;
  if (mod !== (shortcut.mod ?? false) || other) return false;
  if (event.altKey !== (shortcut.alt ?? false)) return false;
  if (shiftMatters(shortcut.key) && event.shiftKey !== (shortcut.shift ?? false)) return false;
  return true;
}

function keyLabel(key: string, platform: Platform = 'other'): string {
  if (key === 'Enter' && platform === 'mac') return '↵';
  return key.length === 1 ? key.toUpperCase() : key;
}

/** The name of the Ctrl key on the keyboard ("Strg" on a German one). */
export interface KeyNames {
  ctrl: string;
}

const ENGLISH: KeyNames = { ctrl: 'Ctrl' };

/** The keys to show, one `<kbd>` each: `["⌘", "K"]` on macOS, `["Ctrl", "K"]` elsewhere. */
export function shortcutKeys(
  shortcut: Shortcut,
  platform: Platform,
  names: KeyNames = ENGLISH,
): string[] {
  const keys: string[] = [];
  if (platform === 'mac') {
    if (shortcut.alt) keys.push('⌥');
    if (shortcut.shift) keys.push('⇧');
    if (shortcut.mod) keys.push('⌘');
  } else {
    if (shortcut.mod) keys.push(names.ctrl);
    if (shortcut.alt) keys.push('Alt');
    if (shortcut.shift) keys.push('Shift');
  }
  keys.push(keyLabel(shortcut.key, platform));
  return keys;
}

/** The shortcut as one string: `⌘K` on macOS, `Ctrl+K` elsewhere. */
export function shortcutText(
  shortcut: Shortcut,
  platform: Platform,
  names: KeyNames = ENGLISH,
): string {
  return shortcutKeys(shortcut, platform, names).join(platform === 'mac' ? '' : '+');
}

/** The value of `aria-keyshortcuts` (WAI-ARIA: `Meta+K`, `Control+K`, `Shift+F6`). */
export function ariaShortcut(shortcut: Shortcut, platform: Platform): string {
  const keys: string[] = [];
  if (shortcut.mod) keys.push(platform === 'mac' ? 'Meta' : 'Control');
  if (shortcut.alt) keys.push('Alt');
  if (shortcut.shift) keys.push('Shift');
  keys.push(keyLabel(shortcut.key));
  return keys.join('+');
}
