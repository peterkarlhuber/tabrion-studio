/**
 * STU-052 in the shell: the command palette (⌘K / Ctrl+K), the shortcut help (⌘/ / Ctrl+/) and the area change with
 * F6 and Shift+F6 (08 section 7). The same flows run keyboard-only in Chromium in e2e/keyboard.spec.ts.
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { App } from '../App';
import { ProdConnectDialog } from '../components/dialogs/StudioDialogs';
import i18n from '../i18n';
import { setThemePreference } from '../theme';
import type { Workspace } from '../workspace';
import { setPlatformForTests } from './platform';

const health: typeof fetch = async () =>
  new Response(JSON.stringify({ status: 'ok', version: '0.1.0.dev0' }), { status: 200 });

const PROD: Workspace = {
  connection: { name: 'measure-central', environment: 'prod', status: 'connected', latencyMs: 12 },
  tabs: [
    { id: 'q1', title: 'Query 1', connection: 'measure-central', environment: 'prod' },
    { id: 'q2', title: 'parts-dev', connection: 'parts-dev', environment: 'dev' },
  ],
  activeTab: 'q2',
};

function renderApp(workspace?: Workspace) {
  return render(<App fetcher={health} heartbeat={false} workspace={workspace} />);
}

const palette = () => screen.queryByTestId('command-palette');
const help = () => screen.queryByTestId('shortcut-help');

afterEach(async () => {
  setPlatformForTests(null);
  setThemePreference('system');
  window.history.replaceState(null, '', '/');
  window.localStorage.clear();
  await act(async () => {
    await i18n.changeLanguage('en');
  });
});

describe('command palette', () => {
  it('Ctrl+K opens it with the focus in the search, Escape closes it and returns the focus', async () => {
    const user = userEvent.setup();
    renderApp();
    const opener = screen.getByRole('link', { name: 'Settings' });
    opener.focus();
    await user.keyboard('{Control>}k{/Control}');
    const dialog = palette();
    expect(dialog).not.toBeNull();
    expect(dialog).toHaveAccessibleName('Command palette');
    const input = within(dialog as HTMLElement).getByRole('combobox', { name: 'Search commands' });
    expect(input).toHaveFocus();
    expect(input).toHaveAttribute('aria-expanded', 'true');
    await user.keyboard('{Escape}');
    expect(palette()).toBeNull();
    expect(opener).toHaveFocus();
  });

  it('filters, moves with the arrows and runs the active command with Enter', async () => {
    const user = userEvent.setup();
    renderApp();
    await user.keyboard('{Control>}k{/Control}');
    const dialog = palette() as HTMLElement;
    const input = within(dialog).getByRole('combobox');
    expect(within(dialog).getAllByRole('option')).toHaveLength(16);
    expect(within(dialog).getByRole('status')).toHaveTextContent('16 commands');
    await user.type(input, 'go to');
    const options = within(dialog).getAllByRole('option');
    // Every page of the activity bar is offered, in its order, the explorer, SQL editor, migrations and diagnostics
    // pages included.
    expect(options.map((o) => o.textContent)).toEqual([
      'Go to Startcurrent',
      'Go to Connections',
      'Go to Explorer',
      'Go to SQL editor',
      'Go to Query historyCtrlHCtrl+H',
      'Go to Migrations',
      'Go to Diagnostics',
      'Go to Settings',
    ]);
    expect(options[0]).toHaveAttribute('aria-selected', 'true');
    expect(input).toHaveAttribute('aria-activedescendant', options[0]?.id);
    await user.keyboard(
      '{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}',
    );
    expect(input).toHaveAttribute('aria-activedescendant', options[7]?.id);
    await user.keyboard('{ArrowDown}'); // wraps around
    expect(input).toHaveAttribute('aria-activedescendant', options[0]?.id);
    await user.keyboard('{ArrowUp}'); // and back
    expect(input).toHaveAttribute('aria-activedescendant', options[7]?.id);
    await user.keyboard('{Enter}');
    expect(palette()).toBeNull();
    expect(window.location.pathname).toBe('/settings');
    expect(screen.getByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument();
    // The focus moves to the new page's main area, with the ring (STU-053, review I-2).
    await waitFor(() => expect(screen.getByRole('main')).toHaveFocus());
    expect(screen.getByRole('main')).toHaveAttribute('data-region-focus');
  });

  it('says so when no command matches, and Enter does nothing then', async () => {
    const user = userEvent.setup();
    renderApp();
    await user.keyboard('{Control>}k{/Control}');
    const dialog = palette() as HTMLElement;
    await user.type(within(dialog).getByRole('combobox'), 'drop table');
    expect(within(dialog).queryAllByRole('option')).toHaveLength(0);
    expect(dialog).toHaveTextContent('No command matches.');
    expect(within(dialog).getByRole('combobox')).not.toHaveAttribute('aria-activedescendant');
    await user.keyboard('{Enter}');
    expect(palette()).not.toBeNull();
    expect(window.location.pathname).toBe('/');
  });

  it('Tab stays in the search (combobox pattern)', async () => {
    const user = userEvent.setup();
    renderApp();
    await user.keyboard('{Control>}k{/Control}');
    const input = within(palette() as HTMLElement).getByRole('combobox');
    await user.tab();
    expect(input).toHaveFocus();
    await user.tab({ shift: true });
    expect(input).toHaveFocus();
  });

  it('a click runs a command; the theme and the language change at once', async () => {
    const user = userEvent.setup();
    renderApp();
    await user.click(screen.getByRole('button', { name: 'Command palette (Ctrl+K)' }));
    await user.click(within(palette() as HTMLElement).getByRole('option', { name: 'Theme: Dark' }));
    expect(document.documentElement.dataset.theme).toBe('dark');
    await user.keyboard('{Control>}k{/Control}');
    await user.type(within(palette() as HTMLElement).getByRole('combobox'), 'deutsch');
    await user.keyboard('{Enter}');
    expect(document.documentElement.lang).toBe('de');
    expect(screen.getByRole('button', { name: 'Befehlspalette (Strg+K)' })).toBeInTheDocument();
  });

  it('shows the macOS keys on a Mac and listens for ⌘K there', async () => {
    setPlatformForTests('mac');
    const user = userEvent.setup();
    renderApp();
    const button = screen.getByRole('button', { name: 'Command palette (⌘K)' });
    expect(button).toHaveAttribute('aria-keyshortcuts', 'Meta+K');
    await user.keyboard('{Control>}k{/Control}');
    expect(palette()).toBeNull();
    await user.keyboard('{Meta>}k{/Meta}');
    expect(palette()).not.toBeNull();
  });

  it('the page shortcuts do nothing while another dialog is open', async () => {
    const user = userEvent.setup();
    render(
      <>
        <App fetcher={health} heartbeat={false} />
        <ProdConnectDialog
          open
          name="measure-central"
          timeoutSeconds={15}
          onConnect={() => undefined}
          onCancel={() => undefined}
        />
      </>,
    );
    await user.keyboard('{Control>}k{/Control}{Control>}/{/Control}{F6}');
    expect(palette()).toBeNull();
    expect(help()).toBeNull();
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
  });
});

describe('events the page shortcuts leave alone (L-3 of stu-052-td021-review.md)', () => {
  it('Ctrl+K during an IME composition does nothing', () => {
    renderApp();
    act(() => {
      window.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, isComposing: true, bubbles: true }),
      );
    });
    expect(palette()).toBeNull();
  });

  it('Ctrl+K that another handler already took does nothing', () => {
    renderApp();
    const event = new KeyboardEvent('keydown', {
      key: 'k',
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    });
    event.preventDefault();
    act(() => {
      window.dispatchEvent(event);
    });
    expect(palette()).toBeNull();
    act(() => {
      window.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }),
      );
    });
    expect(palette()).not.toBeNull(); // the probe: the same shortcut without preventDefault opens it
  });

  it('Escape in the palette does not reach the page behind it', async () => {
    const user = userEvent.setup();
    const seen = vi.fn();
    const listener = (event: KeyboardEvent) => {
      if (event.key === 'Escape') seen();
    };
    window.addEventListener('keydown', listener);
    try {
      renderApp();
      await user.keyboard('{Control>}k{/Control}');
      await user.keyboard('{Escape}');
      expect(palette()).toBeNull();
      expect(seen).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener('keydown', listener);
    }
  });
});

describe('shortcut help', () => {
  it('Ctrl+/ opens it; it lists the shortcuts that work now', async () => {
    const user = userEvent.setup();
    renderApp();
    await user.keyboard('{Control>}/{/Control}');
    const dialog = help() as HTMLElement;
    expect(dialog).toHaveAccessibleName('Keyboard shortcuts');
    expect(dialog.querySelector('[data-action="close"]')).toHaveFocus();
    // The shortcuts of the whole page, then those of the SQL editor's page (STU-012) and of the explorer's (STU-010),
    // each group with its heading.
    expect(
      within(dialog)
        .getAllByRole('columnheader')
        .map((th) => th.textContent),
    ).toEqual(['Action', 'Keys', 'Whole page', 'SQL editor', 'Explorer']);
    const rows = within(dialog)
      .getAllByRole('row')
      .filter((row) => within(row).queryAllByRole('cell').length > 0);
    expect(rows.map((row) => within(row).getAllByRole('cell')[1]?.textContent)).toEqual([
      'CtrlKCtrl+K',
      'Ctrl/Ctrl+/',
      'F6F6',
      'ShiftF6Shift+F6',
      'CtrlHCtrl+H',
      'CtrlEnterCtrl+Enter',
      'CtrlShiftEnterCtrl+Shift+Enter',
      'Ctrl.Ctrl+.',
      'AltTAlt+T',
      'AltWAlt+W',
      'CtrlSCtrl+S',
      'AltShiftFAlt+Shift+F',
      'CtrlPCtrl+P',
    ]);
    await user.keyboard('{Escape}');
    expect(help()).toBeNull();
  });

  it('opens from the palette and from the header button', async () => {
    const user = userEvent.setup();
    renderApp();
    await user.keyboard('{Control>}k{/Control}');
    await user.type(within(palette() as HTMLElement).getByRole('combobox'), 'shortcuts');
    await user.keyboard('{Enter}');
    expect(palette()).toBeNull();
    expect(help()).not.toBeNull();
    await user.keyboard('{Escape}');
    const button = screen.getByRole('button', { name: 'Keyboard shortcuts (Ctrl+/)' });
    expect(button).toHaveAttribute('aria-keyshortcuts', 'Control+/');
    await user.click(button);
    expect(help()).not.toBeNull();
  });

  it('is German in German, with the German key name', async () => {
    await act(async () => {
      await i18n.changeLanguage('de');
    });
    const user = userEvent.setup();
    renderApp();
    await user.keyboard('{Control>}/{/Control}');
    expect(help()).toHaveAccessibleName('Tastenkürzel');
    expect(help()).toHaveTextContent('Strg+K');
  });
});

describe('F6 moves between the areas', () => {
  it('header, navigation, tabs, main area, status bar, and around', async () => {
    const user = userEvent.setup();
    renderApp(PROD);
    await user.keyboard('{F6}');
    expect(screen.getByRole('link', { name: 'Tabrion Studio' })).toHaveFocus();
    await user.keyboard('{F6}');
    expect(screen.getByRole('link', { name: 'Start' })).toHaveFocus(); // the current page
    await user.keyboard('{F6}');
    expect(screen.getByRole('tab', { name: /parts-dev/ })).toHaveFocus(); // the selected tab
    await user.keyboard('{F6}');
    expect(screen.getByRole('tabpanel')).toHaveFocus();
    expect(screen.getByRole('tabpanel')).toHaveAttribute('data-region-focus'); // the ring shows
    await user.keyboard('{F6}');
    expect(screen.getByRole('contentinfo')).toHaveFocus();
    expect(screen.getByRole('tabpanel')).not.toHaveAttribute('data-region-focus');
    await user.keyboard('{F6}');
    expect(screen.getByRole('link', { name: 'Tabrion Studio' })).toHaveFocus();
    await user.keyboard('{Shift>}{F6}{/Shift}');
    expect(screen.getByRole('contentinfo')).toHaveFocus();
  });

  it('skips the tabs without a workspace and starts from where the focus is', async () => {
    const user = userEvent.setup();
    renderApp();
    screen.getByRole('link', { name: 'Settings' }).focus();
    await user.keyboard('{F6}');
    expect(screen.getByRole('main')).toHaveFocus();
    await user.keyboard('{Shift>}{F6}{/Shift}{Shift>}{F6}{/Shift}');
    expect(screen.getByRole('link', { name: 'Tabrion Studio' })).toHaveFocus();
  });
});
