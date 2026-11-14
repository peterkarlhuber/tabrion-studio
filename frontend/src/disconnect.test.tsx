/**
 * QA-S1 S1-32, owner decision of 2026-11-14 14:13Z (option A): after "Disconnect" in the connection manager, "Run" in
 * the SQL editor opens no connection and sends no query. The banner "Disconnected" with "Reconnect" shows; only
 * "Reconnect" there opens the connection again (D-02 first for PROD; Cancel keeps it disconnected) and then runs the
 * query the user asked for. The header, the banner, the editor and the connection manager read the same state (the
 * page's connection session). A connection the server closed (a loss, TD-034) is opened again by itself for DEV, as
 * before. The same flows run against the real server in e2e/disconnect-backend.spec.ts.
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { forwardRef, useImperativeHandle, useRef } from 'react';

import { App } from './App';
import { createDraftsServer } from './harness/drafts';
import { PROFILES, SETTINGS } from './harness/profiles';
import { createQueryServer } from './harness/queries';
import i18n from './i18n';
import { navigate } from './router';
import type { SqlEditorHandle, SqlEditorProps } from './views/editor/SqlEditor';

// CodeMirror needs a layout jsdom does not have: a text area stands in for it (as in EditorView.test.tsx).
vi.mock('./views/editor/SqlEditor', () => ({
  SqlEditor: forwardRef<SqlEditorHandle, SqlEditorProps>(function FakeEditor(props, ref) {
    const area = useRef<HTMLTextAreaElement>(null);
    useImperativeHandle(ref, () => ({
      selection: () => {
        const el = area.current as HTMLTextAreaElement;
        return { from: el.selectionStart, to: el.selectionEnd, head: el.selectionEnd };
      },
      select: () => undefined,
      replace: () => undefined,
      closeCompletion: () => undefined,
      focus: () => area.current?.focus(),
    }));
    return (
      <textarea
        ref={area}
        aria-label={props.label}
        value={props.value}
        onChange={(event) => props.onChange(event.target.value)}
      />
    );
  }),
}));

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800);
});

afterEach(async () => {
  window.history.replaceState(null, '', '/');
  localStorage.clear();
  sessionStorage.clear();
  await act(async () => {
    await i18n.changeLanguage('en');
  });
});

type Call = { method: string; url: string; body: string };

/**
 * A fake server: the profiles, one editor tab on `profile`, the open connections (`GET`, `POST` and `DELETE
 * /api/connections`) and the query engine. `closeOnServer` closes the connections of a profile as the server would
 * (a loss: the page is not told).
 */
function server(
  profile: string,
  sql = 'select 1',
  /** Holds a request until the promise it returns settles (the answer is made after that). */
  hold: (call: Call) => Promise<void> | null = () => null,
) {
  const drafts = createDraftsServer({
    unavailable: false,
    seed: {
      tabs: [{ id: 'd1', title: 'Query 1', profile_id: profile, sql, saved_sql: '' }],
      active_tab: 'd1',
    },
  });
  const queries = createQueryServer();
  const open = new Map<
    string,
    { connection_id: string; profile_id: string; environment: string }
  >();
  const calls: Call[] = [];
  const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? init.body : '';
    calls.push({ method, url, body });
    await hold({ method, url, body });
    if (url === '/api/profiles') return new Response(JSON.stringify(PROFILES));
    if (url === '/api/settings') return new Response(JSON.stringify(SETTINGS));
    if (url === '/api/connections' && method === 'GET') {
      return new Response(JSON.stringify({ connections: [...open.values()] }));
    }
    if (url === '/api/connections' && method === 'DELETE') {
      open.delete((JSON.parse(body) as { connection_id: string }).connection_id);
      return new Response(null, { status: 204 });
    }
    if (url === '/api/queries' && method === 'POST') {
      const id = (JSON.parse(body) as { connection_id: string }).connection_id;
      if (![...open.keys()].includes(id)) {
        return new Response(
          JSON.stringify({ code: 'studio.connection_not_found', kind: 'not_found', status: 404 }),
          { status: 404 },
        );
      }
    }
    const stored = drafts.handle(url, init);
    if (stored !== null) return stored;
    const answer = await queries.handle(url, init);
    if (answer === undefined || answer === null) return new Response(null, { status: 404 });
    if (url === '/api/connections' && method === 'POST' && answer.ok) {
      const doc = (await answer.clone().json()) as {
        connection_id: string;
        profile_id: string;
        environment: string;
      };
      open.set(doc.connection_id, doc);
    }
    return answer;
  }) as typeof fetch;
  const closeOnServer = (profileId: string) => {
    for (const [id, item] of open) if (item.profile_id === profileId) open.delete(id);
  };
  return { fetcher, calls, closeOnServer };
}

const header = () =>
  within(screen.getByRole('banner')).getByRole('button', { name: /connection/i });
const opens = (calls: Call[]) =>
  calls.filter((call) => call.method === 'POST' && call.url === '/api/connections');
const starts = (calls: Call[]) =>
  calls.filter((call) => call.method === 'POST' && call.url === '/api/queries');

async function go(path: '/sql' | '/connections') {
  act(() => navigate(path));
  if (path === '/sql') await screen.findByRole('textbox', { name: 'SQL of Query 1' });
  else await screen.findAllByTestId('connection-item');
}

async function run(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByTestId('editor-run'));
}

/** Run once (the editor opens the connection), then "Disconnect" in the connection manager. */
async function runThenDisconnect(profile: string, name: string, confirmProd = false) {
  const user = userEvent.setup();
  const fake = server(profile);
  window.history.replaceState(null, '', '/sql');
  render(<App fetcher={fake.fetcher} heartbeat={false} connectionCheckMs={0} />);
  await screen.findByRole('textbox', { name: 'SQL of Query 1' });
  await run(user);
  if (confirmProd) {
    const dialog = await screen.findByRole('dialog', { name: 'Production database' });
    await user.click(within(dialog).getByRole('button', { name: 'Connect' }));
  }
  await waitFor(() => expect(screen.getByTestId('result-status')).toHaveTextContent(/^3 rows/));
  // The connection the run opened is the header's connection.
  expect(header()).toHaveTextContent(`${name}, connected`);

  await go('/connections');
  const item = screen
    .getAllByTestId('connection-item')
    .find((element) => element.textContent?.includes(name));
  await user.click(item as HTMLElement);
  const detail = screen.getByTestId('connection-detail');
  await user.click(await within(detail).findByRole('button', { name: 'Disconnect' }));
  await within(detail).findByRole('button', { name: 'Connect' });
  expect(header()).toHaveTextContent('No connection');
  expect(screen.queryByTestId('connection-lost')).toBeNull();
  return { user, ...fake };
}

describe('S1-32: Run after the user disconnected', () => {
  it('DEV: opens nothing and runs nothing; Reconnect opens the connection and runs the query', async () => {
    const { user, calls } = await runThenDisconnect('project:parts-dev', 'parts-dev');
    const before = { opens: opens(calls).length, starts: starts(calls).length };

    await go('/sql');
    await run(user);
    const banner = await screen.findByTestId('connection-lost');
    expect(banner).toHaveAttribute('data-cause', 'disconnected');
    expect(banner).toHaveTextContent('Disconnected: parts-dev');
    expect(banner).toHaveTextContent('You disconnected this connection.');
    expect(screen.getByTestId('editor-notice')).toHaveTextContent(
      'You disconnected parts-dev. Nothing was run.',
    );
    expect(header()).toHaveTextContent('No connection');
    // A second "Run" asks again; still nothing goes to the server.
    await run(user);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(opens(calls)).toHaveLength(before.opens);
    expect(starts(calls)).toHaveLength(before.starts);
    expect(screen.getAllByTestId('connection-lost')).toHaveLength(1);

    await user.click(within(banner).getByRole('button', { name: 'Reconnect' }));
    await waitFor(() => expect(screen.queryByTestId('connection-lost')).toBeNull());
    await waitFor(() => expect(starts(calls)).toHaveLength(before.starts + 1));
    expect(opens(calls)).toHaveLength(before.opens + 1);
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(screen.getByTestId('result-status')).toHaveTextContent(/^3 rows/));
    expect(screen.getByTestId('editor-notice')).toHaveTextContent('');
    expect(header()).toHaveTextContent('parts-dev, connected');
    // The query ran on the connection "Reconnect" opened.
    const last = starts(calls).at(-1) as Call;
    expect((JSON.parse(last.body) as { connection_id: string }).connection_id).toBe(
      'c-project:parts-dev',
    );
    // The connection manager shows the same.
    await go('/connections');
    const item = screen
      .getAllByTestId('connection-item')
      .find((element) => element.textContent?.includes('parts-dev'));
    await user.click(item as HTMLElement);
    await within(screen.getByTestId('connection-detail')).findByRole('button', {
      name: 'Disconnect',
    });
  });

  it('PROD: Reconnect asks D-02 again; Cancel keeps it disconnected and runs nothing', async () => {
    const { user, calls } = await runThenDisconnect(
      'user:measure-central',
      'measure-central',
      true,
    );
    const before = { opens: opens(calls).length, starts: starts(calls).length };

    await go('/sql');
    await run(user);
    const banner = await screen.findByTestId('connection-lost');
    expect(banner).toHaveTextContent('Disconnected: measure-central');
    expect(within(banner).getByTestId('env-badge')).toHaveAttribute('data-env', 'prod');
    // The banner comes first: no D-02 before "Reconnect".
    expect(screen.queryByRole('dialog')).toBeNull();

    await user.click(within(banner).getByRole('button', { name: 'Reconnect' }));
    let dialog = await screen.findByRole('dialog', { name: 'Production database' });
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByTestId('connection-lost')).toBeVisible();
    expect(header()).toHaveTextContent('No connection');
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(opens(calls)).toHaveLength(before.opens);
    expect(starts(calls)).toHaveLength(before.starts);

    await user.click(
      within(screen.getByTestId('connection-lost')).getByRole('button', { name: 'Reconnect' }),
    );
    dialog = await screen.findByRole('dialog', { name: 'Production database' });
    await user.click(within(dialog).getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(screen.queryByTestId('connection-lost')).toBeNull());
    await waitFor(() => expect(starts(calls)).toHaveLength(before.starts + 1));
    expect(
      opens(calls)
        .slice(before.opens)
        .map((call) => JSON.parse(call.body) as unknown),
    ).toEqual([{ profile_id: 'user:measure-central', confirmed_environment: 'prod' }]);
    await waitFor(() => expect(screen.getByTestId('result-status')).toHaveTextContent(/^3 rows/));
    expect(header()).toHaveTextContent('measure-central, connected');
    // D-02 once: the run itself asks nothing more.
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('a profile the user did not disconnect runs as before; a loss on DEV is opened again by itself', async () => {
    const user = userEvent.setup();
    const { fetcher, calls, closeOnServer } = server('project:parts-dev');
    window.history.replaceState(null, '', '/sql');
    render(<App fetcher={fetcher} heartbeat={false} connectionCheckMs={0} />);
    await screen.findByRole('textbox', { name: 'SQL of Query 1' });
    await run(user);
    await waitFor(() => expect(screen.getByTestId('result-status')).toHaveTextContent(/^3 rows/));
    // The server closed the connection (another tab's "Disconnect", a restart): a loss, not the user's disconnect.
    closeOnServer('project:parts-dev');
    await run(user);
    await waitFor(() => expect(opens(calls)).toHaveLength(2));
    await waitFor(() => expect(starts(calls)).toHaveLength(3));
    await waitFor(() => expect(screen.getByTestId('result-status')).toHaveTextContent(/^3 rows/));
    expect(header()).toHaveTextContent('parts-dev, connected');
  });
});

/** A promise the test settles by hand. */
function gate() {
  let release: () => void = () => undefined;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

/** "Connections" → the profile → "Disconnect" (the answer to it may be held by the fake server). */
async function disconnectIn(user: ReturnType<typeof userEvent.setup>, name: string) {
  await go('/connections');
  const item = screen
    .getAllByTestId('connection-item')
    .find((element) => element.textContent?.includes(name));
  await user.click(item as HTMLElement);
  const detail = screen.getByTestId('connection-detail');
  await user.click(await within(detail).findByRole('button', { name: 'Disconnect' }));
}

describe('S1-32: the edges of "Disconnect" (review of #106)', () => {
  it('a run in flight: a statement that finds the connection closed by "Disconnect" opens nothing again', async () => {
    const user = userEvent.setup();
    // Statement 2's start waits until the test lets it go: "Disconnect" happens while it is on its way.
    const second = gate();
    let startsSeen = 0;
    const { fetcher, calls } = server('project:parts-dev', 'select 1;\nselect 2', (call) =>
      call.method === 'POST' && call.url === '/api/queries' && ++startsSeen === 2
        ? second.promise
        : null,
    );
    window.history.replaceState(null, '', '/sql');
    render(<App fetcher={fetcher} heartbeat={false} connectionCheckMs={0} />);
    await screen.findByRole('textbox', { name: 'SQL of Query 1' });
    await user.click(await screen.findByTestId('editor-run-script'));
    await waitFor(() => expect(starts(calls)).toHaveLength(2));
    expect(opens(calls)).toHaveLength(1);

    await disconnectIn(user, 'parts-dev');
    await waitFor(() => expect(calls.some((call) => call.method === 'DELETE')).toBe(true));
    // Statement 2 now reaches a server that no longer has the connection: `studio.connection_not_found`.
    second.release();
    const banner = await screen.findByTestId('connection-lost');
    expect(banner).toHaveAttribute('data-cause', 'disconnected');
    expect(banner).toHaveTextContent('Disconnected: parts-dev');
    await new Promise((resolve) => setTimeout(resolve, 50));
    // A loss would open DEV again by itself; the user's disconnect does not.
    expect(opens(calls)).toHaveLength(1);
    expect(starts(calls)).toHaveLength(2);
  });

  it('"Run" while the close request is still on its way is held too', async () => {
    const user = userEvent.setup();
    const closing = gate();
    const { fetcher, calls } = server('project:parts-dev', 'select 1', (call) =>
      call.method === 'DELETE' ? closing.promise : null,
    );
    window.history.replaceState(null, '', '/sql');
    render(<App fetcher={fetcher} heartbeat={false} connectionCheckMs={0} />);
    await screen.findByRole('textbox', { name: 'SQL of Query 1' });
    await user.click(await screen.findByTestId('editor-run'));
    await waitFor(() => expect(screen.getByTestId('result-status')).toHaveTextContent(/^3 rows/));
    const before = { opens: opens(calls).length, starts: starts(calls).length };

    await disconnectIn(user, 'parts-dev');
    await waitFor(() => expect(calls.some((call) => call.method === 'DELETE')).toBe(true));
    // The server has not answered the close yet (the connection is still open there): "Run" must not use or reopen it.
    await go('/sql');
    await user.click(await screen.findByTestId('editor-run'));
    const banner = await screen.findByTestId('connection-lost');
    expect(banner).toHaveAttribute('data-cause', 'disconnected');
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(opens(calls)).toHaveLength(before.opens);
    expect(starts(calls)).toHaveLength(before.starts);
    closing.release();
  });
});
