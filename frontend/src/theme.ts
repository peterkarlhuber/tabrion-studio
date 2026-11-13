/**
 * Theme and density (08 section 8: light and dark, both complete, the system setting by default; compact density by
 * default). The resolved theme is written to <html data-theme>, which src/styles/tokens.css reads, and the logo
 * variant follows it (assets/branding/README.md: no CSS filters on the logo).
 */
import { useSyncExternalStore } from 'react';

import { readPreference, writePreference } from './lib/storage';

export type ThemePreference = 'system' | 'light' | 'dark';
export type Theme = 'light' | 'dark';
export type Density = 'compact' | 'normal';

interface State {
  preference: ThemePreference;
  theme: Theme;
  density: Density;
}

const DARK_QUERY = '(prefers-color-scheme: dark)';
const listeners = new Set<() => void>();

function systemTheme(): Theme {
  try {
    return window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

function readThemePreference(): ThemePreference {
  const stored = readPreference('theme');
  return stored === 'light' || stored === 'dark' ? stored : 'system';
}

function readDensity(): Density {
  return readPreference('density') === 'normal' ? 'normal' : 'compact';
}

function resolve(preference: ThemePreference): Theme {
  return preference === 'system' ? systemTheme() : preference;
}

let state: State = (() => {
  const preference = readThemePreference();
  return { preference, theme: resolve(preference), density: readDensity() };
})();

/**
 * Switch the theme with transitions off (TD-047). A button's background has a transition but its text color has none,
 * so without this the text took the new theme at once while the background still faded from the old one: for
 * `--tb-duration-fast` the label of a primary button was dark text on the light theme's dark blue, below the 4.5:1
 * of WCAG 1.4.3 (axe found it in WebKit right after "Theme: Dark"). `data-theme-switching` turns every transition off
 * (global.css); the forced style flush computes the new theme's values while it is set, so removing it afterwards
 * starts no transition.
 */
function switchTheme(root: HTMLElement, theme: Theme): void {
  if (root.dataset.theme === theme) return;
  root.dataset.themeSwitching = '';
  root.dataset.theme = theme;
  void root.offsetHeight; // the style flush
  delete root.dataset.themeSwitching;
}

function apply(next: State): void {
  state = next;
  const root = document.documentElement;
  switchTheme(root, next.theme);
  root.dataset.density = next.density;
  for (const listener of listeners) listener();
}

/** Apply the stored preferences and follow the system setting while the preference is "system". */
export function initTheme(): void {
  apply({ ...state });
  try {
    window.matchMedia(DARK_QUERY).addEventListener('change', () => {
      if (state.preference === 'system') apply({ ...state, theme: systemTheme() });
    });
  } catch {
    // No matchMedia (tests): the light theme stays.
  }
}

export function setThemePreference(preference: ThemePreference): void {
  writePreference('theme', preference === 'system' ? null : preference);
  apply({ ...state, preference, theme: resolve(preference) });
}

export function setDensity(density: Density): void {
  writePreference('density', density === 'compact' ? null : density);
  apply({ ...state, density });
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useTheme(): State {
  return useSyncExternalStore(subscribe, () => state);
}
