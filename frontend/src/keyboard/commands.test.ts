/** STU-052: the palette's commands and its filter. */
import { act } from '@testing-library/react';

import i18n from '../i18n';
import { buildCommands, filterCommands, type Command } from './commands';

const context = (openHelp = () => undefined) => ({
  t: i18n.t.bind(i18n),
  path: '/settings',
  theme: 'dark' as const,
  density: 'compact' as const,
  openHelp,
});

afterEach(async () => {
  await act(async () => {
    await i18n.changeLanguage('en');
  });
});

const labels = (commands: Command[]) => commands.map((c) => c.label);

describe('buildCommands', () => {
  it('offers the views, the appearance, the languages and the help', () => {
    expect(labels(buildCommands(context()))).toEqual([
      'Go to Start',
      'Go to Connections',
      'Go to Settings',
      'Theme: System',
      'Theme: Light',
      'Theme: Dark',
      'Density: Compact',
      'Density: Normal',
      'Language: English',
      'Language: Deutsch',
      'Show keyboard shortcuts',
    ]);
  });

  it('marks the current page, theme, density and language', () => {
    const current = buildCommands(context()).filter((c) => c.current);
    expect(current.map((c) => c.id)).toEqual([
      'go:/settings',
      'theme:dark',
      'density:compact',
      'language:en',
    ]);
  });

  it('is translated', async () => {
    await act(async () => {
      await i18n.changeLanguage('de');
    });
    expect(labels(buildCommands(context())).slice(0, 4)).toEqual([
      'Gehe zu Start',
      'Gehe zu Verbindungen',
      'Gehe zu Einstellungen',
      'Theme: System',
    ]);
  });

  it('the help command opens the help and shows its shortcut', () => {
    const openHelp = vi.fn();
    const help = buildCommands(context(openHelp)).find((c) => c.id === 'help:shortcuts');
    expect(help?.shortcut).toEqual({ key: '/', mod: true });
    help?.run();
    expect(openHelp).toHaveBeenCalledOnce();
  });
});

describe('filterCommands', () => {
  const commands = buildCommands(context());

  it('keeps every command for an empty query', () => {
    expect(filterCommands(commands, '   ')).toEqual(commands);
  });

  it('matches every word, in any order, without case', () => {
    expect(labels(filterCommands(commands, 'SETT go'))).toEqual(['Go to Settings']);
    expect(labels(filterCommands(commands, 'theme d'))).toEqual(['Theme: Dark']);
  });

  it('ignores accents', async () => {
    await act(async () => {
      await i18n.changeLanguage('de');
    });
    const german = buildCommands(context());
    expect(labels(filterCommands(german, 'tastenkurzel'))).toEqual(['Tastenkürzel anzeigen']);
  });

  it('finds nothing for an unknown word', () => {
    expect(filterCommands(commands, 'drop table')).toEqual([]);
  });
});
