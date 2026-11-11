import { useTranslation } from 'react-i18next';

import { detectPlatform, type KeyNames, type Platform } from './shortcuts';

let override: Platform | null = null;

/** Tests show the macOS keys without faking the browser's navigator. */
export function setPlatformForTests(platform: Platform | null): void {
  override = platform;
}

/** ⌘ on macOS, Ctrl elsewhere (08 section 10). The platform does not change while the page is open. */
export function useKeyboardPlatform(): Platform {
  return override ?? detectPlatform();
}

/** The key names of the UI language ("Strg" in German), for shortcutKeys() and shortcutText(). */
export function useKeyNames(): KeyNames {
  const { t } = useTranslation();
  return { ctrl: t('keyboard.keys.ctrl') };
}
