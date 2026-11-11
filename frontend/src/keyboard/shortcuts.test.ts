/** STU-052: the shortcut table matches and shows the keys of the platform (08 sections 7 and 10). */
import {
  ariaShortcut,
  detectPlatform,
  matches,
  SHORTCUTS,
  shortcutKeys,
  shortcutText,
} from './shortcuts';

const key = (
  k: string,
  mods: Partial<Record<'ctrl' | 'meta' | 'shift' | 'alt', boolean>> = {},
) => ({
  key: k,
  ctrlKey: mods.ctrl ?? false,
  metaKey: mods.meta ?? false,
  shiftKey: mods.shift ?? false,
  altKey: mods.alt ?? false,
});

describe('detectPlatform', () => {
  it.each([
    [{ userAgentData: { platform: 'macOS' } }, 'mac'],
    [{ platform: 'MacIntel' }, 'mac'],
    [{ platform: 'iPad' }, 'mac'],
    [{ platform: 'Win32' }, 'other'],
    [{ platform: 'Linux x86_64' }, 'other'],
    [{ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)' }, 'mac'],
    [{}, 'other'],
    [undefined, 'other'],
  ])('%j is %s', (nav, platform) => {
    expect(detectPlatform(nav)).toBe(platform);
  });
});

describe('matches', () => {
  it('takes Ctrl on Windows and Linux, ⌘ on macOS', () => {
    expect(matches(key('k', { ctrl: true }), SHORTCUTS.palette, 'other')).toBe(true);
    expect(matches(key('K', { ctrl: true }), SHORTCUTS.palette, 'other')).toBe(true); // Caps Lock
    expect(matches(key('k', { meta: true }), SHORTCUTS.palette, 'other')).toBe(false);
    expect(matches(key('k', { meta: true }), SHORTCUTS.palette, 'mac')).toBe(true);
    expect(matches(key('k', { ctrl: true }), SHORTCUTS.palette, 'mac')).toBe(false);
  });

  it('refuses extra or missing modifiers', () => {
    expect(matches(key('k'), SHORTCUTS.palette, 'other')).toBe(false);
    expect(matches(key('k', { ctrl: true, alt: true }), SHORTCUTS.palette, 'other')).toBe(false);
    expect(matches(key('k', { ctrl: true, shift: true }), SHORTCUTS.palette, 'other')).toBe(false);
    expect(matches(key('k', { ctrl: true, meta: true }), SHORTCUTS.palette, 'other')).toBe(false);
    expect(matches(key('j', { ctrl: true }), SHORTCUTS.palette, 'other')).toBe(false);
  });

  it('ignores Shift for punctuation (Ctrl+/ is Ctrl+Shift+7 on a German keyboard)', () => {
    expect(matches(key('/', { ctrl: true }), SHORTCUTS.help, 'other')).toBe(true);
    expect(matches(key('/', { ctrl: true, shift: true }), SHORTCUTS.help, 'other')).toBe(true);
    expect(matches(key('/'), SHORTCUTS.help, 'other')).toBe(false);
  });

  it('tells F6 and Shift+F6 apart', () => {
    expect(matches(key('F6'), SHORTCUTS.nextRegion, 'other')).toBe(true);
    expect(matches(key('F6', { shift: true }), SHORTCUTS.nextRegion, 'other')).toBe(false);
    expect(matches(key('F6', { shift: true }), SHORTCUTS.previousRegion, 'mac')).toBe(true);
    expect(matches(key('F6'), SHORTCUTS.previousRegion, 'mac')).toBe(false);
    expect(matches(key('F6', { ctrl: true }), SHORTCUTS.nextRegion, 'other')).toBe(false);
  });
});

describe('display', () => {
  it('shows the keys of the platform', () => {
    expect(shortcutKeys(SHORTCUTS.palette, 'other')).toEqual(['Ctrl', 'K']);
    expect(shortcutKeys(SHORTCUTS.palette, 'mac')).toEqual(['⌘', 'K']);
    expect(shortcutText(SHORTCUTS.help, 'other')).toBe('Ctrl+/');
    expect(shortcutText(SHORTCUTS.help, 'mac')).toBe('⌘/');
    expect(shortcutText(SHORTCUTS.previousRegion, 'other')).toBe('Shift+F6');
    expect(shortcutText(SHORTCUTS.previousRegion, 'mac')).toBe('⇧F6');
    expect(shortcutText({ key: 'f', alt: true, shift: true }, 'mac')).toBe('⌥⇧F');
    expect(shortcutText({ key: 'w', alt: true, mod: true }, 'other')).toBe('Ctrl+Alt+W');
    expect(shortcutText(SHORTCUTS.palette, 'other', { ctrl: 'Strg' })).toBe('Strg+K');
    expect(shortcutText(SHORTCUTS.palette, 'mac', { ctrl: 'Strg' })).toBe('⌘K');
  });

  it('writes aria-keyshortcuts in the WAI-ARIA form', () => {
    expect(ariaShortcut(SHORTCUTS.palette, 'other')).toBe('Control+K');
    expect(ariaShortcut(SHORTCUTS.palette, 'mac')).toBe('Meta+K');
    expect(ariaShortcut(SHORTCUTS.previousRegion, 'other')).toBe('Shift+F6');
  });

  it('never takes a browser-reserved shortcut (08 section 7)', () => {
    const reserved = ['w', 't', 'n', 'l', 'r', 'q'];
    for (const shortcut of Object.values(SHORTCUTS)) {
      const mod = 'mod' in shortcut && shortcut.mod;
      expect(mod && reserved.includes(shortcut.key)).toBe(false);
    }
  });
});
