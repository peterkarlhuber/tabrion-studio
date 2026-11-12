/**
 * STU-008: the connection manager lists the profiles, writes only the user's own ones with the digest it read, sends
 * a password once to the secret route and never shows it, and sends Content-Type: application/json on every write,
 * DELETE included (review of STU-007).
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { readProblem } from '../../api/problem';
import { parseProfiles } from '../../api/profiles';
import { SettingsView } from '../Views';
import { ConnectionsView } from './ConnectionsView';

const DIGEST = 'b'.repeat(64);
const HOSTILE = `evil${String.fromCodePoint(0x202e)}gpj.exe`;

function listing(extra: Record<string, unknown> = {}) {
  return {
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
        id: `user:${HOSTILE}`,
        source: 'user',
        name: HOSTILE,
        engine: 'postgresql',
        environment: 'prod',
        host: 'db.internal',
        database: 'm',
        user: 'ro',
        sslmode: 'verify-full',
        password: { source: 'keyring', target: '' },
        secret: { source: 'keyring', has_secret: null },
      },
    ],
    project_state: 'trusted',
    issues: { user: [] },
    user_file: { digest: DIGEST, hand_written: false },
    ...extra,
  };
}

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

function server(answers: Record<string, (call: Call) => Response> = {}, list: unknown = listing()) {
  const calls: Call[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = {
      url: String(input),
      method: init?.method ?? 'GET',
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
    };
    calls.push(call);
    const answer = answers[`${call.method} ${call.url}`];
    if (answer) return answer(call);
    if (call.url === '/api/profiles' && call.method === 'GET') {
      return new Response(JSON.stringify(list), { status: 200 });
    }
    if (call.url === '/api/secrets') {
      return new Response(JSON.stringify({ keyring: { available: true } }), { status: 200 });
    }
    if (call.url === '/api/settings' && call.method === 'GET') {
      return new Response(
        JSON.stringify({
          settings: {
            log_sql: false,
            limits: { max_rows: 10000, statement_timeout_ms: 30000 },
          },
          issues: [],
          file: { digest: null, hand_written: false },
        }),
        { status: 200 },
      );
    }
    return new Response(JSON.stringify({ id: 'user:x', digest: 'c'.repeat(64) }), { status: 200 });
  });
  return { fetcher: fetcher as unknown as typeof fetch, calls };
}

function writes(calls: Call[]) {
  return calls.filter((call) => call.method !== 'GET');
}

const problem = (status: number, code: string, issues?: unknown) =>
  new Response(JSON.stringify({ code, kind: 'config', status, issues }), { status });

describe('connection manager', () => {
  it('groups project and user profiles and escapes names from files', async () => {
    const { fetcher } = server();
    render(<ConnectionsView fetcher={fetcher} />);
    const list = await screen.findByRole('navigation', { name: 'Connection profiles' });
    const items = within(list).getAllByTestId('connection-item');
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining('parts-dev'),
      expect.stringContaining('evil\\u202Egpj.exe'),
    ]);
    // A project profile: no edit or delete (Studio never writes the project file).
    const detail = screen.getByTestId('connection-detail');
    expect(within(detail).queryByRole('button', { name: 'Edit' })).toBeNull();
    // STU-009 is there: testing and connecting work for every profile, project profiles included.
    expect(within(detail).getByRole('button', { name: 'Connect' })).toBeEnabled();
    expect(within(detail).getByRole('button', { name: 'Test connection' })).toBeEnabled();
    await userEvent.click(items[1] as HTMLElement);
    expect(
      within(screen.getByTestId('connection-detail')).getByRole('button', { name: 'Edit' }),
    ).toBeEnabled();
  });

  it('filters by name and shows an empty state without profiles', async () => {
    const { fetcher } = server();
    render(<ConnectionsView fetcher={fetcher} />);
    await userEvent.type(await screen.findByRole('searchbox'), 'parts');
    expect(screen.getAllByTestId('connection-item')).toHaveLength(1);
    const empty = server({}, listing({ profiles: [] }));
    render(<ConnectionsView fetcher={empty.fetcher} />);
    expect(await screen.findByText('No connections yet')).toBeVisible();
  });

  it('creates a PostgreSQL profile in three steps; the password goes once to the secret route', async () => {
    const { fetcher, calls } = server({
      'POST /api/profiles': () =>
        new Response(JSON.stringify({ id: 'user:measure', digest: 'c'.repeat(64) }), {
          status: 201,
        }),
      'PUT /api/profiles/user%3Ameasure/secret': () => new Response(null, { status: 204 }),
      'POST /api/connection-tests': () =>
        new Response(
          JSON.stringify({ ok: true, engine: 'postgresql', server_version: '17.6', facts: {} }),
          { status: 200 },
        ),
      'GET /api/profiles': () =>
        new Response(
          JSON.stringify(
            listing({
              profiles: [
                ...listing().profiles,
                {
                  id: 'user:measure',
                  source: 'user',
                  name: 'measure',
                  engine: 'postgresql',
                  environment: 'prod',
                  host: 'db.internal',
                  database: 'm',
                  password: { source: 'keyring', target: '' },
                  secret: { source: 'keyring', has_secret: null },
                },
              ],
            }),
          ),
          { status: 200 },
        ),
    });
    render(<ConnectionsView fetcher={fetcher} />);
    await userEvent.click(await screen.findByRole('button', { name: 'New connection' }));
    const dialog = screen.getByRole('dialog', { name: 'New connection' });
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    await userEvent.click(within(dialog).getByRole('radio', { name: 'PostgreSQL' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Next' }));
    // Required fields are checked before the next step.
    expect(within(dialog).getByLabelText('Host')).toHaveAttribute('aria-invalid', 'true');
    await userEvent.type(within(dialog).getByLabelText('Host'), 'db.internal');
    await userEvent.type(within(dialog).getByLabelText('Database'), 'm');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Next' }));
    expect(within(dialog).getByTestId('wizard-step-2')).toBeVisible();
    expect(within(dialog).getByRole('radio', { name: 'System keyring' })).toBeChecked();
    await userEvent.type(within(dialog).getByLabelText('Password'), 'pw-canary');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Next' }));
    await userEvent.type(within(dialog).getByLabelText('Name'), 'measure');
    await userEvent.click(within(dialog).getByRole('radio', { name: 'PROD' }));
    // The test runs on the saved profile (the typed password goes only to the secret route, OA-010).
    expect(
      within(dialog).getByRole('checkbox', { name: 'Test the connection after saving' }),
    ).toBeChecked();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(writes(calls)).toHaveLength(3));
    const [create, secret, test] = writes(calls);
    expect(create?.body).toEqual({
      profile: {
        name: 'measure',
        engine: 'postgresql',
        environment: 'prod',
        host: 'db.internal',
        database: 'm',
        password: 'keyring:',
      },
      digest: DIGEST,
      overwrite: false,
    });
    expect(JSON.stringify(create?.body)).not.toContain('pw-canary');
    expect(secret?.body).toEqual({ secret: 'pw-canary', store: 'keyring' });
    expect(test?.url).toBe('/api/connection-tests');
    expect(test?.body).toEqual({ profile_id: 'user:measure' });
    expect(
      await within(screen.getByTestId('connection-detail')).findByText(/Connection works/),
    ).toHaveTextContent('PostgreSQL 17.6');
    for (const call of writes(calls)) {
      expect(call.headers['Content-Type']).toBe('application/json');
    }
    expect(screen.getByTestId('connections-status')).toHaveTextContent('measure saved.');
  });

  it('takes a pasted URL apart: the fields fill, the password goes to step 2, never into the file', async () => {
    const { fetcher, calls } = server({
      'POST /api/profiles': () =>
        new Response(JSON.stringify({ id: 'user:pasted', digest: 'c'.repeat(64) }), {
          status: 201,
        }),
      'PUT /api/profiles/user%3Apasted/secret': () => new Response(null, { status: 204 }),
    });
    render(<ConnectionsView fetcher={fetcher} />);
    await userEvent.click(await screen.findByRole('button', { name: 'New connection' }));
    const dialog = screen.getByRole('dialog', { name: 'New connection' });
    await userEvent.click(within(dialog).getByRole('radio', { name: 'PostgreSQL' }));
    const url = within(dialog).getByLabelText('Or paste a URL');
    await userEvent.type(url, 'mysql://h/d');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Use URL' }));
    expect(within(dialog).getByTestId('wizard-url-state')).toHaveTextContent(
      'must start with postgresql://',
    );
    expect(within(dialog).getByLabelText('Host')).toHaveValue('');
    await userEvent.clear(url);
    await userEvent.type(
      url,
      'postgresql://ro:pw-canary@db.internal:6432/m?sslmode=verify-full{Enter}',
    );
    expect(url).toHaveValue('');
    expect(within(dialog).getByTestId('wizard-url-state')).toHaveTextContent(
      'Its password is in the next step',
    );
    expect(within(dialog).getByLabelText('Host')).toHaveValue('db.internal');
    expect(within(dialog).getByLabelText('Port')).toHaveValue('6432');
    expect(within(dialog).getByLabelText('Database')).toHaveValue('m');
    expect(within(dialog).getByLabelText('User')).toHaveValue('ro');
    expect(within(dialog).getByLabelText('TLS mode')).toHaveValue('verify-full');
    expect(dialog.textContent).not.toContain('pw-canary');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Next' }));
    expect(within(dialog).getByRole('radio', { name: 'System keyring' })).toBeChecked();
    expect(within(dialog).getByLabelText('Password')).toHaveValue('pw-canary');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Next' }));
    await userEvent.type(within(dialog).getByLabelText('Name'), 'pasted');
    await userEvent.click(within(dialog).getByRole('radio', { name: 'TEST' }));
    await userEvent.click(
      within(dialog).getByRole('checkbox', { name: 'Test the connection after saving' }),
    );
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    const [create, secret] = writes(calls);
    expect(create?.body).toMatchObject({
      profile: {
        host: 'db.internal',
        port: 6432,
        database: 'm',
        user: 'ro',
        sslmode: 'verify-full',
        password: 'keyring:',
      },
    });
    expect(JSON.stringify(create?.body)).not.toContain('pw-canary');
    expect(secret?.body).toEqual({ secret: 'pw-canary', store: 'keyring' });
    // Unchecked: no connection test after saving.
    expect(calls.some((call) => call.url === '/api/connection-tests')).toBe(false);
  });

  it('shows the server issues at their fields and goes back to their step', async () => {
    const { fetcher } = server({
      'POST /api/profiles': () =>
        problem(422, 'studio.profile_invalid', [
          { pointer: 'profile.path', code: 'config.network_path' },
          { pointer: 'evil<script>', code: 'config.x' },
        ]),
    });
    render(<ConnectionsView fetcher={fetcher} />);
    await userEvent.click(await screen.findByRole('button', { name: 'New connection' }));
    const dialog = screen.getByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText('File'), '//host/share/x.db');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Next' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Next' }));
    await userEvent.type(within(dialog).getByLabelText('Name'), 'x');
    await userEvent.click(within(dialog).getByRole('radio', { name: 'DEV' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    const path = await within(dialog).findByLabelText('File');
    expect(path).toHaveAttribute('aria-invalid', 'true');
    expect(within(dialog).getByText('Network paths and devices are not allowed.')).toBeVisible();
    expect(dialog.textContent).not.toContain('evil<script>');
  });

  it('a hand-written file is rewritten only after the user confirms it', async () => {
    let count = 0;
    const { fetcher, calls } = server({
      'POST /api/profiles': () =>
        count++ === 0
          ? problem(409, 'studio.config_hand_written')
          : new Response(JSON.stringify({ id: 'user:x', digest: DIGEST }), { status: 201 }),
    });
    render(<ConnectionsView fetcher={fetcher} />);
    await userEvent.click(await screen.findByRole('button', { name: 'New connection' }));
    const dialog = screen.getByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText('File'), '/data/x.db');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Next' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Next' }));
    await userEvent.type(within(dialog).getByLabelText('Name'), 'x');
    await userEvent.click(within(dialog).getByRole('radio', { name: 'TEST' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    const rewrite = await within(dialog).findByRole('button', { name: 'Rewrite file and save' });
    expect(within(dialog).getByRole('alert')).toHaveTextContent('written by hand');
    await userEvent.click(rewrite);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(writes(calls).map((call) => (call.body as { overwrite: boolean }).overwrite)).toEqual([
      false,
      true,
    ]);
  });

  it('deletes a user profile after a confirmation whose default is Cancel', async () => {
    const { fetcher, calls } = server({
      'DELETE /api/profiles': () =>
        new Response(JSON.stringify({ digest: DIGEST }), { status: 200 }),
    });
    render(<ConnectionsView fetcher={fetcher} />);
    const items = await screen.findAllByTestId('connection-item');
    await userEvent.click(items[1] as HTMLElement);
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    const [remove] = writes(calls);
    expect(remove?.body).toEqual({ id: `user:${HOSTILE}`, digest: DIGEST, overwrite: false });
    expect(remove?.headers['Content-Type']).toBe('application/json');
  });

  it('enters and removes a password; DELETE of the secret sends JSON', async () => {
    const { fetcher, calls } = server({
      [`PUT /api/profiles/${encodeURIComponent(`user:${HOSTILE}`)}/secret`]: () =>
        new Response(null, { status: 204 }),
      [`DELETE /api/profiles/${encodeURIComponent(`user:${HOSTILE}`)}/secret`]: () =>
        new Response(null, { status: 204 }),
    });
    render(<ConnectionsView fetcher={fetcher} />);
    const items = await screen.findAllByTestId('connection-item');
    await userEvent.click(items[1] as HTMLElement);
    await userEvent.click(screen.getByRole('button', { name: 'Enter password' }));
    const dialog = screen.getByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText('Password'), 'typed');
    await userEvent.click(within(dialog).getByRole('radio', { name: 'Only for this session' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save password' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await userEvent.click(screen.getByRole('button', { name: 'Remove saved password' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    const [put, remove] = writes(calls);
    expect(put?.body).toEqual({ secret: 'typed', store: 'session' });
    expect(remove?.method).toBe('DELETE');
    expect(remove?.headers['Content-Type']).toBe('application/json');
    expect(remove?.body).toEqual({});
    expect(document.body.textContent).not.toContain('typed');
  });
});

describe('server settings', () => {
  it('saves the limits and SQL logging to settings.toml', async () => {
    const { fetcher, calls } = server({
      'PUT /api/settings': () =>
        new Response(JSON.stringify({ digest: DIGEST, settings: {} }), { status: 200 }),
    });
    render(<SettingsView fetcher={fetcher} />);
    const rows = await screen.findByLabelText('Maximum rows per result');
    await userEvent.clear(rows);
    await userEvent.type(rows, '500');
    await userEvent.click(
      screen.getByRole('checkbox', { name: 'Write SQL statements to the log' }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(await screen.findByTestId('settings-saved')).toBeVisible();
    const [put] = writes(calls);
    expect(put?.body).toEqual({
      settings: { log_sql: true, limits: { max_rows: 500, statement_timeout_ms: 30000 } },
      digest: null,
      overwrite: false,
    });
  });

  it('refuses a malformed number before it is sent', async () => {
    const { fetcher, calls } = server();
    render(<SettingsView fetcher={fetcher} />);
    const rows = await screen.findByLabelText('Maximum rows per result');
    await userEvent.clear(rows);
    await userEvent.type(rows, '1e3');
    await userEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(rows).toHaveAttribute('aria-invalid', 'true');
    expect(writes(calls)).toEqual([]);
  });
});

describe('parsing', () => {
  it('drops malformed profiles and issues', async () => {
    expect(parseProfiles(null)).toBeNull();
    const parsed = parseProfiles({ profiles: [{ id: 'x' }, ...listing().profiles] });
    expect(parsed?.profiles).toHaveLength(2);
    const problemDoc = await readProblem(
      problem(422, 'studio.profile_invalid', [
        { pointer: 'profile.host', code: 'config.required' },
        { pointer: 'profile.<b>', code: 'config.required' },
        { pointer: 'profile.host', code: 'free text' },
      ]),
    );
    expect(problemDoc.issues).toEqual([{ pointer: 'profile.host', code: 'config.required' }]);
  });
});
