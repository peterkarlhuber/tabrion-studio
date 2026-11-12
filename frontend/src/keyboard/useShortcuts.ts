/**
 * The page-wide shortcuts (08 section 7): ⌘K / Ctrl+K the command palette, ⌘/ / Ctrl+/ the shortcut help, F6 and
 * Shift+F6 the next and previous area. While a modal dialog is open (a confirmation, the palette itself) the page
 * shortcuts do nothing: the dialog owns the keyboard.
 */
import { useEffect, useRef } from 'react';

import { useKeyboardPlatform } from './platform';
import { moveRegion } from './regions';
import { matches, SHORTCUTS } from './shortcuts';

export interface ShortcutHandlers {
  openPalette: () => void;
  openHelp: () => void;
}

function dialogOpen(): boolean {
  return document.querySelector('dialog[open]') !== null;
}

export function useShortcuts(handlers: ShortcutHandlers): void {
  const platform = useKeyboardPlatform();
  const current = useRef(handlers);
  useEffect(() => {
    current.current = handlers;
  }, [handlers]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || dialogOpen()) return;
      if (matches(event, SHORTCUTS.palette, platform)) {
        event.preventDefault();
        current.current.openPalette();
      } else if (matches(event, SHORTCUTS.help, platform)) {
        event.preventDefault();
        current.current.openHelp();
      } else if (matches(event, SHORTCUTS.nextRegion, platform)) {
        event.preventDefault();
        moveRegion(1);
      } else if (matches(event, SHORTCUTS.previousRegion, platform)) {
        event.preventDefault();
        moveRegion(-1);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [platform]);
}
