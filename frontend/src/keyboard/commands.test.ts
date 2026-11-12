/** STU-052: the palette's commands and its filter. */
import { act } from '@testing-library/react';

import i18n from '../i18n';
import { buildCommands, filterCommands, type Command } from './commands';
import { setEditorActions } from './editorActions';

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
      'Go to SQL editor',
      'Go to Migrations',
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
    expect(labels(buildCommands(context())).slice(0, 6)).toEqual([
      'Gehe zu Start',
      'Gehe zu Verbindungen',
      'Gehe zu SQL-Editor',
      'Gehe zu Migrationen',
      'Gehe zu Einstellungen',
      'Darstellung: System',
    ]);
    // L-1 of stu-052-td021-review.md: the word of the settings view finds the theme commands.
    expect(labels(filterCommands(buildCommands(context()), 'darstellung dunkel'))).toEqual([
      'Darstellung: Dunkel',
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

describe('SQL editor commands (STU-012)', () => {
  afterEach(() => setEditorActions(null));

  it('are offered on the editor page while it is shown; Cancel only while a query runs', () => {
    const ran: string[] = [];
    let running = false;
    setEditorActions({
      run: () => ran.push('run'),
      runScript: () => ran.push('runScript'),
      cancel: () => ran.push('cancel'),
      newTab: () => ran.push('newTab'),
      closeTab: () => ran.push('closeTab'),
      save: () => ran.push('save'),
      running: () => running,
    });
    const onEditor = { ...context(), path: '/sql' };
    expect(labels(buildCommands(context())).filter((l) => l.startsWith('Run'))).toEqual([]);
    expect(labels(buildCommands(onEditor)).slice(0, 5)).toEqual([
      'Run',
      'Run script',
      'New editor tab',
      'Close editor tab',
      'Save as .sql',
    ]);
    running = true;
    const commands = buildCommands(onEditor);
    expect(labels(commands)).toContain('Cancel query');
    for (const command of commands.filter((c) => c.group === 'editor')) command.run();
    expect(ran).toEqual(['run', 'runScript', 'cancel', 'newTab', 'closeTab', 'save']);
    setEditorActions(null);
    expect(labels(buildCommands(onEditor))).not.toContain('Run');
  });
});
