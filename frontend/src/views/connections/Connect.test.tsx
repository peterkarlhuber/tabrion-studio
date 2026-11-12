/**
 * STU-008 with STU-009: the connection test and "Connect" in the connection manager (08 sections 4.2, 5 and 6).
 *
 * - The test shows the facts the server read (version, role, superuser, TLS, encoding, time zone; for SQLite foreign
 *   keys and journal), escaped, and warns about a privileged role.
 * - "Connect" opens the read-only connection and makes it the header's connection; a PROD profile asks D-02 first
 *   (default "Cancel"), once per page session; "Disconnect" closes it.
 * - A failure shows the page's own text with what to check (never the server's message); a missing password opens the
 *   password dialog and the action runs again after it was saved.
 * - The migration status view shares the D-02 confirmation and the header's connection.
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { App } from '../../App';
import { parseOpened, parseOpenList, parseTest } from '../../api/connections';
import { readProblem } from '../../api/problem';
import i18n from '../../i18n';

const BIDI = String.fromCodePoint(0x202e);

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
      statement_timeout_ms: 20000,
      password: { source: 'keyring', target: '' },
      secret: { source: 'keyring', has_secret: null },
    },
  ],
  project_state: 'trusted',
  issues: { user: [] },
  user_file: { digest: 'b'.repeat(64), hand_written: false },
};

const PG_TEST = {
  ok: true,
  engine: 'postgresql',
  environment: 'prod',
  server_version: '17.6',
  latency_ms: 12.4,
  read_only: true,
  facts: {
    role: `ro${BIDI}x`,
    superuser: false,
    bypass_rls: false,
    encoding: 'UTF8',
    timezone: 'Europe/Vienna',
    tls: true,
  },
};

type Call = { url: string; method: string; body: unknown; headers: Record<string, string> };
/** An answer; null falls through to the default answer. */
type Answer = (call: Call) => Response | null;

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function server(answers: Record<string, Answer | Answer[]> = {}) {
  const calls: Call[] = [];
  const open = new Map<string, Record<string, unknown>>();
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = {
      url: String(input),
      method: init?.method ?? 'GET',
      body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
      headers: (init?.headers ?? {}) as Record<string, string>,
    };
    calls.push(call);
    const answer = answers[`${call.method} ${call.url}`];
    const next = Array.isArray(answer) ? (answer.length > 1 ? answer.shift() : answer[0]) : answer;
    const given = next ? next(call) : null;
    if (given !== null) return given;
    if (call.url === '/api/profiles') return json(PROFILES);
    if (call.url === '/api/secrets') return json({ keyring: { available: true } });
    if (call.url === '/api/heartbeat') return new Response(null, { status: 204 });
    if (call.url === '/api/connections' && call.method === 'GET') {
      return json({ connections: [...open.values()] });
    }
    if (call.url === '/api/connections' && call.method === 'POST') {
      const id = (call.body as { profile_id: string }).profile_id;
      const profile = PROFILES.profiles.find((item) => item.id === id);
      const opened = {
        connection_id: `c-${id}`,
        profile_id: id,
        engine: profile?.engine,
        environment: profile?.environment,
        read_only: true,
        url: 'sqlite:///x',
        opened_at: 1,
      };
      open.set(opened.connection_id, opened);
      return json(opened, 201);
    }
    if (call.url === '/api/connections' && call.method === 'DELETE') {
      open.delete((call.body as { connection_id: string }).connection_id);
      return new Response(null, { status: 204 });
    }
    if (call.url === '/api/connection-tests') return json(PG_TEST);
    if (call.url === '/api/connections/c-user%3Ameasure/migrations') {
      return json({ connection_id: 'c-user:measure', engine: 'postgresql', formats: [] });
    }
    if (call.url === '/api/project/code-trust') {
      return json({ state: 'none', digest: null, revision_files: [] });
    }
    return json({ status: 'ok', version: '0.1.0.dev0' });
  });
  return { fetcher: fetcher as unknown as typeof fetch, calls, open };
}

const writes = (calls: Call[]) => calls.filter((call) => call.method !== 'GET');
const problem = (status: number, body: Record<string, unknown>) => () => json(body, status);

async function manager(fetcher: typeof fetch) {
  window.history.replaceState(null, '', '/connections');
  render(<App fetcher={fetcher} heartbeat={false} />);
  await screen.findAllByTestId('connection-item');
}

async function select(name: string) {
  const item = screen
    .getAllByTestId('connection-item')
    .find((element) => element.textContent?.includes(name));
  await userEvent.click(item as HTMLElement);
  return screen.getByTestId('connection-detail');
}

const header = () => screen.getByRole('banner');

afterEach(async () => {
  window.history.replaceState(null, '', '/');
  await act(async () => {
    await i18n.changeLanguage('en');
  });
});

describe('connection test', () => {
  it('shows the facts the server read, escaped, and closes nothing it did not open', async () => {
    const { fetcher, calls } = server();
    await manager(fetcher);
    const detail = await select('measure');
    await userEvent.click(within(detail).getByRole('button', { name: 'Test connection' }));
    const result = await within(detail).findByText(/Connection works/);
    expect(result).toHaveTextContent(
      'Connection works: 12.4 ms · PostgreSQL 17.6 · role ro\\u202Ex · no superuser · TLS on · encoding UTF8 · time zone Europe/Vienna',
    );
    expect(result.textContent).not.toContain(BIDI);
    expect(within(detail).queryByText(/superuser or bypasses/)).toBeNull();
    expect(writes(calls)).toEqual([
      expect.objectContaining({
        url: '/api/connection-tests',
        body: { profile_id: 'user:measure' },
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
      }),
    ]);
    // A test is no connection: the header still has none.
    expect(within(header()).getByRole('button', { name: /No connection/ })).toBeInTheDocument();
  });

  it('shows the SQLite facts and warns about a privileged role', async () => {
    const { fetcher } = server({
      'POST /api/connection-tests': [
        () =>
          json({
            ok: true,
            engine: 'sqlite',
            server_version: '3.45.1',
            latency_ms: 0.4,
            facts: { foreign_keys: true, journal_mode: 'wal', encoding: 'UTF-8' },
          }),
        () => json({ ...PG_TEST, facts: { ...PG_TEST.facts, superuser: true } }),
      ],
    });
    await manager(fetcher);
    let detail = await select('parts-dev');
    await userEvent.click(within(detail).getByRole('button', { name: 'Test connection' }));
    expect(await within(detail).findByText(/Connection works/)).toHaveTextContent(
      'Connection works: 0.4 ms · SQLite 3.45.1 · foreign keys on · journal wal · encoding UTF-8',
    );
    detail = await select('measure');
    await userEvent.click(within(detail).getByRole('button', { name: 'Test connection' }));
    expect(await within(detail).findByText(/Connection works/)).toHaveTextContent('superuser');
    expect(within(detail).getByText(/superuser or bypasses row-level security/)).toBeVisible();
  });

  it.each([
    [
      { code: 'db.connection', kind: 'connection', status: 502, sqlstate: '08001' },
      /Check the host and the port/,
    ],
    [
      { code: 'db.connection', kind: 'connection', status: 502, sqlstate: '28P01' },
      /refused the sign-in/,
    ],
    [{ code: 'db.corrupt', kind: 'corrupt', status: 500 }, /not a SQLite database/],
    [
      { code: 'studio.database_missing', kind: 'not_found', status: 404 },
      /SQLite file does not exist/,
    ],
    [
      { code: 'db.brand_new', kind: 'brand_new', status: 500, detail: 'server <b>text</b>' },
      /reported an error \(brand_new\)/,
    ],
  ])('a failed test says what to check, never the server text (%o)', async (body, text) => {
    const { fetcher } = server({ 'POST /api/connection-tests': problem(body.status, body) });
    await manager(fetcher);
    const detail = await select('parts-dev');
    await userEvent.click(within(detail).getByRole('button', { name: 'Test connection' }));
    const failed = await within(detail).findByText(/Test failed/);
    expect(failed).toHaveTextContent(text);
    expect(failed.textContent).not.toContain('<b>');
  });
});

describe('connect', () => {
  it('connects a DEV profile at once; the header shows it; Disconnect closes it', async () => {
    const { fetcher, calls } = server();
    await manager(fetcher);
    const detail = await select('parts-dev');
    await userEvent.click(within(detail).getByRole('button', { name: 'Connect' }));
    const switcher = await within(header()).findByTestId('connection-switcher');
    await waitFor(() => expect(switcher).toHaveTextContent('parts-dev, connected'));
    expect(within(header()).getByTestId('env-badge')).toHaveAttribute('data-env', 'dev');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.title).toBe('[DEV] parts-dev – Tabrion Studio');
    expect(screen.getByTestId('connections-status')).toHaveTextContent(
      'Connected to parts-dev (read-only).',
    );
    expect(within(detail).getByTestId('connection-state')).toHaveTextContent('open (read-only)');
    await userEvent.click(within(detail).getByRole('button', { name: 'Disconnect' }));
    await within(header()).findByRole('button', { name: /No connection/ });
    expect(writes(calls).map((call) => [call.method, call.body])).toEqual([
      ['POST', { profile_id: 'project:parts-dev' }],
      ['DELETE', { connection_id: 'c-project:parts-dev' }],
    ]);
    await waitFor(() =>
      expect(within(detail).getByTestId('connection-state')).toHaveTextContent('not open'),
    );
  });

  it('asks D-02 before the first PROD connection of the session; Cancel opens nothing', async () => {
    const { fetcher, calls } = server();
    await manager(fetcher);
    const detail = await select('measure');
    await userEvent.click(within(detail).getByRole('button', { name: 'Connect' }));
    let dialog = screen.getByRole('dialog', { name: 'Production database' });
    // The profile's own statement timeout (20 s), not the 15 s default.
    expect(dialog).toHaveAccessibleDescription(
      'measure is marked as PROD. Studio stays read-only; queries are limited to 20 s.',
    );
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(writes(calls)).toEqual([]);
    expect(within(detail).queryByText(/Connecting/)).toBeNull();
    await userEvent.click(within(detail).getByRole('button', { name: 'Connect' }));
    dialog = screen.getByRole('dialog', { name: 'Production database' });
    await userEvent.click(within(dialog).getByRole('button', { name: 'Connect' }));
    await waitFor(() =>
      expect(within(header()).getByTestId('connection-switcher')).toHaveTextContent('connected'),
    );
    expect(document.title).toBe('[PROD] measure – Tabrion Studio');
    expect(within(header()).getByTestId('access-mode')).toHaveTextContent('Writing locked');
    // Once per session: after Disconnect, connecting again does not ask.
    await userEvent.click(within(detail).getByRole('button', { name: 'Disconnect' }));
    await within(header()).findByRole('button', { name: /No connection/ });
    await userEvent.click(within(detail).getByRole('button', { name: 'Connect' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() =>
      expect(writes(calls).filter((call) => call.method === 'POST')).toHaveLength(2),
    );
  });

  it('a failed connect keeps the header as it was and names the remedy', async () => {
    const { fetcher } = server({
      'POST /api/connections': [
        () => null,
        problem(502, { code: 'db.connection', kind: 'connection', status: 502, sqlstate: '28000' }),
      ],
    });
    await manager(fetcher);
    await userEvent.click(
      within(await select('parts-dev')).getByRole('button', { name: 'Connect' }),
    );
    await waitFor(() =>
      expect(within(header()).getByTestId('connection-switcher')).toHaveTextContent(
        'parts-dev, connected',
      ),
    );
    const detail = await select('measure');
    await userEvent.click(within(detail).getByRole('button', { name: 'Connect' }));
    await userEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Connect' }),
    );
    expect(await within(detail).findByText(/Not connected/)).toHaveTextContent(
      'Not connected: The database refused the sign-in. Check the user and the password.',
    );
    expect(within(header()).getByTestId('connection-switcher')).toHaveTextContent(
      'parts-dev, connected',
    );
  });

  it('a missing password opens the password dialog, then connects', async () => {
    const { fetcher, calls } = server({
      'POST /api/connection-tests': [
        problem(409, { code: 'studio.secret_missing', kind: 'config', status: 409 }),
        () => json(PG_TEST),
      ],
      'PUT /api/profiles/user%3Ameasure/secret': () => new Response(null, { status: 204 }),
    });
    await manager(fetcher);
    const detail = await select('measure');
    await userEvent.click(within(detail).getByRole('button', { name: 'Test connection' }));
    const dialog = await screen.findByRole('dialog', { name: 'Password for measure' });
    await userEvent.type(within(dialog).getByLabelText('Password'), 'pw-canary');
    await userEvent.click(within(dialog).getByRole('radio', { name: 'Only for this session' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save password' }));
    expect(await within(detail).findByText(/Connection works/)).toBeVisible();
    expect(writes(calls).map((call) => call.url)).toEqual([
      '/api/connection-tests',
      '/api/profiles/user%3Ameasure/secret',
      '/api/connection-tests',
    ]);
    expect(document.body.textContent).not.toContain('pw-canary');
  });

  it('a connection the server closed (profile changed) leaves the header', async () => {
    const { fetcher, open } = server();
    await manager(fetcher);
    await userEvent.click(
      within(await select('parts-dev')).getByRole('button', { name: 'Connect' }),
    );
    await within(header()).findByTestId('connection-switcher');
    open.clear();
    // Any reload of the list (here: a password saved for another profile) checks the open connections.
    const detail = await select('measure');
    await userEvent.click(within(detail).getByRole('button', { name: 'Remove saved password' }));
    await within(header()).findByRole('button', { name: /No connection/ });
  });

  it('the migration status view shares D-02 and the header connection', async () => {
    const { fetcher, calls } = server();
    await manager(fetcher);
    const detail = await select('measure');
    await userEvent.click(within(detail).getByRole('button', { name: 'Connect' }));
    await userEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Connect' }),
    );
    await within(header()).findByTestId('connection-switcher');
    await userEvent.click(screen.getByRole('link', { name: 'Migrations' }));
    await userEvent.selectOptions(
      await screen.findByLabelText('Connection profile'),
      'user:measure',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Show status' }));
    // Confirmed in the connection manager already: no second D-02 in this session.
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() =>
      expect(
        calls.some((call) => call.url === '/api/connections/c-user%3Ameasure/migrations'),
      ).toBe(true),
    );
    expect(within(header()).getByTestId('connection-switcher')).toHaveTextContent('measure');
  });
});

describe('parsing the connection answers', () => {
  it('drops malformed answers and fields', async () => {
    expect(parseOpened({ connection_id: '' })).toBeNull();
    expect(parseOpened([])).toBeNull();
    expect(parseOpened({ connection_id: 'c', environment: 'staging', profile_id: 3 })).toEqual({
      connectionId: 'c',
      environment: null,
      profileId: null,
    });
    expect(parseOpenList({ connections: [{ connection_id: 'a' }, { x: 1 }, null] })).toHaveLength(
      1,
    );
    expect(parseOpenList({})).toBeNull();
    expect(parseTest({ ok: 'yes' })).toBeNull();
    expect(
      parseTest({ ok: true, engine: 'oracle', latency_ms: Number.NaN, facts: { superuser: 'no' } }),
    ).toEqual({
      ok: true,
      engine: null,
      serverVersion: null,
      latencyMs: null,
      facts: {
        role: null,
        superuser: null,
        bypassRls: null,
        encoding: null,
        timezone: null,
        tls: null,
        foreignKeys: null,
        journalMode: null,
      },
    });
    const withState = await readProblem(json({ code: 'db.connection', sqlstate: '28P01' }, 502));
    expect(withState.sqlstate).toBe('28P01');
    const badState = await readProblem(json({ code: 'db.connection', sqlstate: '28p01;x' }, 502));
    expect(badState.sqlstate).toBeUndefined();
  });
});
