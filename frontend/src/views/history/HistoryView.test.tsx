/** STU-020: the query history page, with the harness's fake history (statements and parameter names only). */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { createHistoryServer } from '../../harness/history';
import { PROFILES } from '../../harness/profiles';
import { takeHandoff } from '../editor/handoff';
import { HistoryView, shownSql } from './HistoryView';

function setup(options: Parameters<typeof createHistoryServer>[0] = {}) {
  const server = createHistoryServer(options);
  const urls: string[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    urls.push(url);
    if (url === '/api/profiles') return new Response(JSON.stringify(PROFILES));
    return server.handle(url, init) ?? new Response(null, { status: 404 });
  };
  render(<HistoryView fetcher={fetcher} />);
  return { server, urls };
}

afterEach(() => {
  takeHandoff();
  window.history.replaceState(null, '', '/');
});

describe('HistoryView (STU-020)', () => {
  it('lists the statements, newest first, with their profile, state and parameter names only', async () => {
    setup();
    const entries = await screen.findAllByTestId('history-entry');
    expect(entries).toHaveLength(3);
    expect(entries[0]).toHaveTextContent('parts-dev');
    expect(entries[0]).toHaveTextContent('DEV');
    expect(entries[0]).toHaveTextContent('1 row');
    expect(within(entries[0] as HTMLElement).getByTestId('history-params')).toHaveTextContent(
      'Parameters (values not kept): :id',
    );
    expect(entries[1]).toHaveTextContent('measure-central');
    expect(entries[1]).toHaveTextContent('PROD');
    expect(entries[1]).toHaveTextContent('Failed');
    expect(within(entries[1] as HTMLElement).queryByTestId('history-params')).toBeNull();
    expect(screen.getByRole('list', { name: 'Statements' })).toBeInTheDocument();
  });

  it('filters by profile and searches the text on the server', async () => {
    const user = userEvent.setup();
    const { urls } = setup();
    await screen.findAllByTestId('history-entry');
    await user.selectOptions(screen.getByTestId('history-profile'), 'user:measure-central');
    await waitFor(() => expect(screen.getAllByTestId('history-entry')).toHaveLength(1));
    expect(urls).toContain('/api/history?profile_id=user%3Ameasure-central&limit=50');
    await user.type(screen.getByTestId('history-search'), 'nothing like this');
    expect(await screen.findByTestId('history-empty')).toHaveTextContent('No statement matches.');
  });

  it('opens a statement in a new editor tab without running it; the values are asked again', async () => {
    const user = userEvent.setup();
    setup();
    const [first] = await screen.findAllByTestId('history-entry');
    await user.click(within(first as HTMLElement).getByTestId('history-open'));
    expect(window.location.pathname).toBe('/sql');
    expect(takeHandoff()).toEqual({
      profileId: 'project:parts-dev',
      title: 'From history',
      sql: 'select * from parts where id = :id',
    });
  });

  it('deletes one entry at once, and everything only after the confirmation (the safe option first)', async () => {
    const user = userEvent.setup();
    const { server } = setup();
    const [first] = await screen.findAllByTestId('history-entry');
    await user.click(within(first as HTMLElement).getByTestId('history-delete'));
    await waitFor(() => expect(screen.getAllByTestId('history-entry')).toHaveLength(2));
    expect(server.log.deletes).toEqual([{ id: 3 }]);
    await user.click(screen.getByTestId('history-delete-all'));
    const dialog = screen.getByRole('dialog', { name: 'Delete the whole history?' });
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));
    expect(server.log.deletes[1]).toEqual({});
  });

  it('says when the history is off', async () => {
    setup({ disabled: true });
    expect(await screen.findByTestId('history-off')).toHaveTextContent(
      'The history is off: new statements are not kept.',
    );
  });

  it('shows a statement as text: invisible characters as escapes, long text cut', () => {
    expect(shownSql('select 1‮;')).not.toContain('‮');
    expect(shownSql('x'.repeat(3000)).length).toBeLessThan(2100);
  });
});
