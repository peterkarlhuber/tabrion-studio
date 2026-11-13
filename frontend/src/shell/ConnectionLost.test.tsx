/**
 * TD-034, 08 section 5 "error recovery": the banner "Connection lost" with "Reconnect", on the whole page. A request
 * on the connection or the page's connection check finds the database gone; "Reconnect" reads the profile again,
 * asks D-02 again for PROD (the lost connection's confirmation is forgotten), and the view goes on with the new
 * connection. The same flows run in Chromium against the harness and the real server (e2e/connection-lost.spec.ts).
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { App } from '../App';
import { explorerAnswer, PG_CATALOG } from '../harness/explorer';
import i18n from '../i18n';

const PROFILES = {
  profiles: [
    {
      id: 'project:parts-dev',
      source: 'project',
      name: 'parts-dev',
      engine: 'sqlite',
      environment: 'dev',
      path: 'data/parts.db',
      password: null,
      secret: { source: null, has_secret: null },
    },
    {
      id: 'user:measure',
      source: 'user',
      name: 'measure',
      engine: 'postgresql',
      environment: 'prod',
      host: 'db.internal',
      database: 'm',
      user: 'ro',
      sslmode: 'verify-full',
      password: { source: 'keyring', target: '' },
      secret: { source: 'keyring', has_secret: true },
    },
  ],
  project_state: 'trusted',
  issues: { user: [] },
  user_file: { digest: 'b'.repeat(64), hand_written: false },
};

type Call = { url: string; method: string; body?: string };
type Answer = (call: Call) => Response | undefined;

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const lost = () =>
  json({ code: 'db.connection', kind: 'connection', status: 502, sqlstate: '08006' }, 502);

/** A fake server: `answer` first, then the profiles, the health check (open) and the harness's explorer. */
function server(answer: Answer = () => undefined) {
  const calls: Call[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = {
      url: String(input),
      method: init?.method ?? 'GET',
      body: typeof init?.body === 'string' ? init.body : undefined,
    };
    calls.push(call);
    const own = answer(call);
    if (own !== undefined) return own;
    if (call.url === '/api/profiles') return json(PROFILES);
    if (call.url.endsWith('/health')) return json({ state: 'open', latency_ms: 1 });
    return explorerAnswer(call.url, call.method, call.body) ?? new Response(null, { status: 404 });
  });
  return { fetcher: fetcher as unknown as typeof fetch, calls };
}

const sent = (calls: Call[]) => calls.map((call) => `${call.method} ${call.url}`);

async function openExplorer(fetcher: typeof fetch, profile: 'sqlite' | 'pg', checkMs = 0) {
  window.history.replaceState(null, '', '/explorer');
  const user = userEvent.setup();
  render(<App fetcher={fetcher} heartbeat={false} connectionCheckMs={checkMs} />);
  await screen.findByRole('option', { name: 'parts-dev (DEV)' });
  if (profile === 'pg') {
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
  }
  await user.click(screen.getByTestId('explorer-open'));
  if (profile === 'pg') {
    const dialog = await screen.findByRole('dialog', { name: 'Production database' });
    await user.click(within(dialog).getByRole('button', { name: 'Connect' }));
  }
  await screen.findByRole('tree');
  return user;
}

afterEach(async () => {
  window.history.replaceState(null, '', '/');
  await act(async () => {
    await i18n.changeLanguage('en');
  });
});

describe('the connection check finds the database gone (DEV)', () => {
  it('shows the banner on every page, marks the header, and Reconnect reads the catalog again', async () => {
    let down = false;
    const { fetcher, calls } = server((call) =>
      down && call.url === '/api/connections/c-sqlite/health' ? lost() : undefined,
    );
    const user = await openExplorer(fetcher, 'sqlite', 25);
    expect(screen.getByTestId('connection-switcher')).toHaveTextContent('connected');
    expect(screen.queryByTestId('connection-lost')).toBeNull();
    // The check runs while the header shows the connection.
    await waitFor(() => expect(sent(calls)).toContain('GET /api/connections/c-sqlite/health'));
    down = true;
    const banner = await screen.findByTestId('connection-lost');
    expect(banner).toHaveAttribute('role', 'alert');
    expect(banner).toHaveTextContent('Connection lost: parts-dev');
    expect(banner).toHaveTextContent('The database does not answer.');
    expect(banner).toHaveTextContent('Nothing was lost: Studio only reads.');
    expect(screen.getByTestId('connection-switcher')).toHaveTextContent('disconnected');
    // The check stops while the connection is lost: no request is made every 25 ms to a database that is gone.
    const checks = sent(calls).filter((call) => call.endsWith('/health')).length;
    await new Promise((resolve) => setTimeout(resolve, 120));
    expect(sent(calls).filter((call) => call.endsWith('/health')).length).toBe(checks);

    down = false;
    const before = calls.length;
    await user.click(within(banner).getByRole('button', { name: 'Reconnect' }));
    await waitFor(() => expect(screen.queryByTestId('connection-lost')).toBeNull());
    // DEV: no D-02. The profile is read again, the connection opened, the catalog read again (keeping the view).
    expect(sent(calls.slice(before))).toEqual(
      expect.arrayContaining([
        'GET /api/profiles',
        'POST /api/connections',
        'GET /api/connections/c-sqlite/catalog?refresh=true',
      ]),
    );
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(screen.getByRole('main')).toHaveFocus());
    expect(screen.getByTestId('connection-switcher')).toHaveTextContent('connected');
  });
});

describe("the connection check and the tab's visibility", () => {
  let visibility: DocumentVisibilityState = 'visible';
  beforeEach(() => {
    visibility = 'visible';
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => visibility,
    });
  });
  afterEach(() => {
    // The jsdom default comes back from the prototype.
    delete (document as unknown as { visibilityState?: unknown }).visibilityState;
  });
  const checks = (calls: Call[]) =>
    sent(calls).filter((call) => call === 'GET /api/connections/c-sqlite/health').length;

  it('asks nothing while the tab is hidden', async () => {
    const { fetcher, calls } = server();
    await openExplorer(fetcher, 'sqlite', 25);
    await waitFor(() => expect(checks(calls)).toBeGreaterThan(0));
    visibility = 'hidden';
    const before = checks(calls);
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(checks(calls)).toBe(before);
  });

  it('asks at once when the tab becomes visible again, not only at the next interval', async () => {
    const { fetcher, calls } = server();
    await openExplorer(fetcher, 'sqlite', 60_000);
    visibility = 'hidden';
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(checks(calls)).toBe(0);
    visibility = 'visible';
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await waitFor(() => expect(checks(calls)).toBe(1));
  });
});

describe('a request finds a PROD database gone', () => {
  it('asks D-02 again on Reconnect, and the explorer goes on with the new connection', async () => {
    let opens = 0;
    const { fetcher, calls } = server((call) => {
      if (call.url === '/api/connections' && call.method === 'POST') {
        opens += 1;
        const id = opens === 1 ? 'c-pg' : 'c-pg2';
        return json({ connection_id: id, profile_id: 'user:measure', environment: 'prod' }, 201);
      }
      if (call.url === '/api/connections/c-pg/catalog?refresh=true') return lost();
      if (call.url === '/api/connections/c-pg2/catalog?refresh=true') {
        return json({ ...PG_CATALOG, connection_id: 'c-pg2' });
      }
      return undefined;
    });
    const user = await openExplorer(fetcher, 'pg');
    await user.click(screen.getByTestId('explorer-refresh'));
    const banner = await screen.findByTestId('connection-lost');
    expect(banner).toHaveTextContent('Connection lost: measure');
    expect(within(banner).getByText('PROD')).toBeInTheDocument();

    // D-02 again; Cancel connects nothing and keeps the banner.
    await user.click(within(banner).getByRole('button', { name: 'Reconnect' }));
    let dialog = await screen.findByRole('dialog', { name: 'Production database' });
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(opens).toBe(1);
    expect(screen.getByTestId('connection-lost')).toBeInTheDocument();

    await user.click(within(banner).getByRole('button', { name: 'Reconnect' }));
    dialog = await screen.findByRole('dialog', { name: 'Production database' });
    await user.click(within(dialog).getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(screen.queryByTestId('connection-lost')).toBeNull());
    const open = calls.filter((call) => call.method === 'POST' && call.url === '/api/connections');
    expect(open.at(-1)?.body).toBe(
      JSON.stringify({ profile_id: 'user:measure', confirmed_environment: 'prod' }),
    );
    await waitFor(() =>
      expect(sent(calls)).toContain('GET /api/connections/c-pg2/catalog?refresh=true'),
    );
    expect(screen.getByRole('tree')).toBeInTheDocument();
  });

  it('says why Reconnect failed while the database is still gone, and keeps the banner', async () => {
    let opens = 0;
    const { fetcher } = server((call) => {
      if (call.url === '/api/connections' && call.method === 'POST') {
        opens += 1;
        if (opens > 1) return lost();
      }
      if (call.url === '/api/connections/c-sqlite/catalog?refresh=true') return lost();
      return undefined;
    });
    const user = await openExplorer(fetcher, 'sqlite');
    await user.click(screen.getByTestId('explorer-refresh'));
    const banner = await screen.findByTestId('connection-lost');
    await user.click(within(banner).getByRole('button', { name: 'Reconnect' }));
    expect(await screen.findByTestId('connection-reconnect-problem')).toHaveTextContent(
      /^Reconnecting failed: /,
    );
    expect(screen.getByTestId('connection-lost')).toBeInTheDocument();
    expect(within(banner).getByRole('button', { name: 'Reconnect' })).toBeEnabled();
  });
});

describe('German', () => {
  it('says "Verbindung getrennt" and "Neu verbinden"', async () => {
    await act(async () => {
      await i18n.changeLanguage('de');
    });
    const { fetcher } = server((call) =>
      call.url === '/api/connections/c-sqlite/catalog?refresh=true' ? lost() : undefined,
    );
    window.history.replaceState(null, '', '/explorer');
    const user = userEvent.setup();
    render(<App fetcher={fetcher} heartbeat={false} connectionCheckMs={0} />);
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.click(screen.getByTestId('explorer-open'));
    await screen.findByRole('tree');
    await user.click(screen.getByTestId('explorer-refresh'));
    const banner = await screen.findByTestId('connection-lost');
    expect(banner).toHaveTextContent('Verbindung getrennt: parts-dev');
    expect(within(banner).getByRole('button', { name: 'Neu verbinden' })).toBeInTheDocument();
  });
});
