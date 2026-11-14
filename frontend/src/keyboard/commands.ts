/**
 * The commands of the command palette (08 section 1: "a command palette for everything", section 7). Every command
 * works on the views that exist now; the explorer, the SQL editor and the history add theirs when they arrive. No
 * command writes to a database. Theme, density and language go through src/appearance.ts like the settings view, so
 * they are also saved to Studio's own settings.toml (`PUT /api/settings`, TD-021).
 */
import type { TFunction } from 'i18next';

import { chooseDensity, chooseLanguage, chooseTheme } from '../appearance';
import { currentLocale, SUPPORTED_LOCALES } from '../i18n';
import { navigate, type Route } from '../router';
import type { Density, ThemePreference } from '../theme';
import { editorActions } from './editorActions';
import { focusRegion } from './regions';
import { detectPlatform, shortcutFor, SHORTCUTS, type Platform, type Shortcut } from './shortcuts';

export type CommandGroup = 'navigate' | 'editor' | 'appearance' | 'help';

export interface Command {
  id: string;
  group: CommandGroup;
  label: string;
  shortcut?: Shortcut;
  /** The command describes the current state (this page, this theme): shown as "current". */
  current?: boolean;
  run: () => void;
}

export interface CommandContext {
  t: TFunction;
  path: string;
  theme: ThemePreference;
  density: Density;
  openHelp: () => void;
  /** The keys shown (the history's differ on a Mac); the browser's platform when not given. */
  platform?: Platform;
}

const PAGES: { path: Route; key: string }[] = [
  { path: '/', key: 'nav.start' },
  { path: '/connections', key: 'nav.connections' },
  { path: '/explorer', key: 'nav.explorer' },
  { path: '/sql', key: 'nav.sql' },
  { path: '/history', key: 'nav.history' },
  { path: '/migrations', key: 'nav.migrations' },
  { path: '/diagnostics', key: 'nav.diagnostics' },
  { path: '/vectors', key: 'nav.vectors' },
  { path: '/settings', key: 'nav.settings' },
];

const THEMES: { value: ThemePreference; key: string }[] = [
  { value: 'system', key: 'settings.themeSystem' },
  { value: 'light', key: 'settings.themeLight' },
  { value: 'dark', key: 'settings.themeDark' },
];

const DENSITIES: { value: Density; key: string }[] = [
  { value: 'compact', key: 'settings.densityCompact' },
  { value: 'normal', key: 'settings.densityNormal' },
];

/** The SQL editor's commands, while its page is shown (STU-012). None of them writes to a database. */
function editorCommands(t: TFunction, path: string): Command[] {
  const actions = editorActions();
  if (path !== '/sql' || actions === null) return [];
  const commands: Command[] = [
    { id: 'editor:run', label: t('editor.actions.run'), shortcut: SHORTCUTS.run, run: actions.run },
    {
      id: 'editor:runScript',
      label: t('editor.actions.runScript'),
      shortcut: SHORTCUTS.runScript,
      run: actions.runScript,
    },
    ...(actions.running()
      ? [
          {
            id: 'editor:cancel',
            label: t('editor.actions.cancel'),
            shortcut: SHORTCUTS.cancel,
            run: actions.cancel,
          },
        ]
      : []),
    {
      id: 'editor:newTab',
      label: t('editor.actions.newTab'),
      shortcut: SHORTCUTS.newTab,
      run: actions.newTab,
    },
    {
      id: 'editor:closeTab',
      label: t('editor.actions.closeTab'),
      shortcut: SHORTCUTS.closeTab,
      run: actions.closeTab,
    },
    {
      id: 'editor:save',
      label: t('editor.actions.save'),
      shortcut: SHORTCUTS.save,
      run: actions.save,
    },
    { id: 'editor:open', label: t('editor.actions.openScript'), run: actions.openScript },
    {
      id: 'editor:format',
      label: t('editor.actions.format'),
      shortcut: SHORTCUTS.format,
      run: actions.format,
    },
  ].map((command) => ({ ...command, group: 'editor' as const }));
  return commands;
}

export function buildCommands({
  t,
  path,
  theme,
  density,
  openHelp,
  platform = detectPlatform(),
}: CommandContext): Command[] {
  const locale = currentLocale();
  return [
    ...editorCommands(t, path),
    ...PAGES.map((page): Command => ({
      id: `go:${page.path}`,
      group: 'navigate',
      label: t('keyboard.commands.goTo', { page: t(page.key) }),
      ...(page.path === '/history' ? { shortcut: shortcutFor('history', platform) } : {}),
      current: path === page.path,
      run: () => {
        navigate(page.path);
        // After the palette has closed and returned the focus to its opener (STU-053, review I-2).
        window.setTimeout(() => focusRegion('main'), 0);
      },
    })),
    ...THEMES.map((item): Command => ({
      id: `theme:${item.value}`,
      group: 'appearance',
      label: t('keyboard.commands.theme', { theme: t(item.key) }),
      current: theme === item.value,
      run: () => chooseTheme(item.value),
    })),
    ...DENSITIES.map((item): Command => ({
      id: `density:${item.value}`,
      group: 'appearance',
      label: t('keyboard.commands.density', { density: t(item.key) }),
      current: density === item.value,
      run: () => chooseDensity(item.value),
    })),
    ...SUPPORTED_LOCALES.map((value): Command => ({
      id: `language:${value}`,
      group: 'appearance',
      // Languages are named in their own language ("Deutsch"), so they can be found in either one.
      label: t('keyboard.commands.language', { language: t(`languages.${value}`) }),
      current: locale === value,
      run: () => chooseLanguage(value),
    })),
    ...(path === '/explorer'
      ? [
          {
            id: 'explorer:filter',
            group: 'navigate' as const,
            label: t('keyboard.commands.explorerFilter'),
            shortcut: SHORTCUTS.explorerFilter,
            // After the palette has returned the focus to its opener: the filter, or the profile picker before a
            // catalog is open (STU-010).
            run: () =>
              window.setTimeout(() => {
                const target =
                  document.querySelector<HTMLElement>('[data-explorer-filter]') ??
                  document.querySelector<HTMLElement>('[data-explorer-profile]');
                target?.focus();
              }, 0),
          },
        ]
      : []),
    {
      id: 'help:shortcuts',
      group: 'help',
      label: t('keyboard.commands.shortcuts'),
      shortcut: SHORTCUTS.help,
      run: openHelp,
    },
  ];
}

/** Lower case without accents, so that "uber" finds "Über". */
function fold(text: string): string {
  return text.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();
}

/** The commands whose label contains every word of the query, in their original order. */
export function filterCommands(commands: Command[], query: string): Command[] {
  const words = fold(query).split(/\s+/).filter(Boolean);
  if (words.length === 0) return commands;
  return commands.filter((command) => {
    const text = fold(command.label);
    return words.every((word) => text.includes(word));
  });
}
