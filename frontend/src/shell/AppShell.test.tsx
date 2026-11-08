/**
 * STU-051: the status displays (08 section 3). A PROD connection is marked in the header, on every tab and in the
 * window title, with a red frame at the top; the badge always carries text (WCAG 1.4.1). Checked in Chromium too
 * (e2e/status.spec.ts).
 */
import { act, render, screen, within } from '@testing-library/react';

import { App } from '../App';
import i18n from '../i18n';
import type { Workspace } from '../workspace';

const health: typeof fetch = vi.fn(
  async () =>
    new Response(JSON.stringify({ status: 'ok', version: '0.1.0.dev0' }), { status: 200 }),
);

const PROD: Workspace = {
  connection: { name: 'measure-central', environment: 'prod', status: 'connected', latencyMs: 12 },
  tabs: [
    { id: 'q1', title: 'Query 1', connection: 'measure-central', environment: 'prod' },
    { id: 'q2', title: 'parts-dev', connection: 'parts-dev', environment: 'dev' },
    { id: 'q3', title: 'parts-pg', connection: 'parts-pg', environment: 'test' },
  ],
  activeTab: 'q1',
};

function renderWith(workspace: Workspace) {
  return render(<App fetcher={health} workspace={workspace} heartbeat={false} />);
}

afterEach(async () => {
  await act(async () => {
    await i18n.changeLanguage('en');
  });
});

describe('PROD marking', () => {
  it('shows the PROD badge in the header', () => {
    renderWith(PROD);
    const header = screen.getByRole('banner');
    const badge = within(header).getByTestId('env-badge');
    expect(badge).toHaveTextContent('PROD');
    expect(badge.textContent).toBe('Environment: PROD');
    expect(badge).toHaveAttribute('data-env', 'prod');
  });

  it('shows the environment on every tab', () => {
    renderWith(PROD);
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((tab) => within(tab).getByTestId('env-badge').textContent)).toEqual([
      'Environment: PROD',
      'Environment: DEV',
      'Environment: TEST',
    ]);
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      'Query 1, Environment: PROD',
      'parts-dev, Environment: DEV',
      'parts-pg, Environment: TEST',
    ]);
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveAccessibleName(
      /^Query 1\s*,\s*Environment:\s*PROD$/,
    );
  });

  it('starts the window title with [PROD]', () => {
    renderWith(PROD);
    expect(document.title).toBe('[PROD] measure-central – Tabrion Studio');
  });

  it('frames the shell in red and locks writing', () => {
    const { container } = renderWith(PROD);
    expect(container.firstElementChild).toHaveAttribute('data-env', 'prod');
    expect(screen.getByTestId('access-mode')).toHaveTextContent('Writing locked');
    expect(screen.getByTestId('tx-state')).toHaveTextContent('No transaction');
    const switcher = screen.getByRole('button', {
      name: /^Connection:\s*measure-central\s*,\s*connected \(12 ms\)$/,
    });
    expect(switcher).toHaveAccessibleDescription('Opens the connection manager.');
  });

  it('marks DEV and TEST without the PROD frame', () => {
    const { container } = renderWith({
      connection: { name: 'parts-pg', environment: 'test', status: 'connecting' },
      tabs: [],
      activeTab: null,
    });
    expect(container.firstElementChild).toHaveAttribute('data-env', 'test');
    expect(within(screen.getByRole('banner')).getByTestId('env-badge')).toHaveTextContent('TEST');
    expect(screen.getByTestId('access-mode')).toHaveTextContent('Read-only');
    expect(document.title).toBe('[TEST] parts-pg – Tabrion Studio');
    expect(screen.queryByRole('tablist')).toBeNull();
  });

  it('is the same in German, with natural German status texts', async () => {
    await act(async () => {
      await i18n.changeLanguage('de');
    });
    renderWith(PROD);
    expect(within(screen.getByRole('banner')).getByTestId('env-badge').textContent).toBe(
      'Umgebung: PROD',
    );
    expect(screen.getByTestId('access-mode')).toHaveTextContent('Schreiben gesperrt');
    expect(screen.getByTestId('tx-state')).toHaveTextContent('Keine Transaktion');
    expect(document.title).toBe('[PROD] measure-central – Tabrion Studio');
  });
});

describe('without a connection', () => {
  it('shows no environment badge, no tabs and the plain title', () => {
    renderWith({ connection: null, tabs: [], activeTab: null });
    expect(screen.queryByTestId('env-badge')).toBeNull();
    expect(screen.queryByRole('tablist')).toBeNull();
    expect(document.title).toBe('Tabrion Studio');
    expect(screen.getByRole('button', { name: 'No connection' })).toHaveAccessibleDescription(
      'Opens the connection manager.',
    );
  });
});
