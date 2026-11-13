/**
 * STU-015: the migration status view. The project's Alembic files run only after the user trusted exactly the files
 * the dialog showed (threat T-16): the trust dialog opens on "Don't trust", sends the digest it showed, shows the new
 * list when the files changed meanwhile (`studio.trust_stale`), and the trust can be taken back. The status opens the
 * profile's read-only connection (D-02 first for PROD) and shows every format; values from the project or the
 * database are escaped (U-1). Every write sends Content-Type: application/json, DELETE included.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { parseCodeTrust, parseMigrations } from '../../api/migrations';
import { MAX_LEVUVO_STEPS, MigrationsView } from './MigrationsView';

const DIGEST = 'a'.repeat(64);
const NEW_DIGEST = 'd'.repeat(64);
const BIDI = String.fromCodePoint(0x202e);
const HOSTILE_FILE = `evil${BIDI}yp.py`;

function trust(extra: Record<string, unknown> = {}) {
  return {
    state: 'untrusted',
    trusted: false,
    format: 'alembic',
    config_file: 'alembic.ini',
    digest: DIGEST,
    script_location: 'migrations',
    revision_files: ['0001_init.py', HOSTILE_FILE],
    import_roots: ['.'],
    module_files: ['shop/models.py', `shop/${HOSTILE_FILE}`],
    issue: null,
    issue_file: null,
    notice: 'server text that the page does not show',
    ...extra,
  };
}

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
      name: `measure${BIDI}`,
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

function alembic(extra: Record<string, unknown> = {}) {
  return {
    format: 'alembic',
    code_trust: 'untrusted',
    code_issue: null,
    script_location: 'migrations',
    current: [`rev${BIDI}1`],
    heads: null,
    pending: null,
    is_current: null,
    unknown: null,
    revisions: 'unknown (project not trusted)',
    problem: null,
    ...extra,
  };
}

function migrations(formats: unknown[]) {
  return { connection_id: 'c1', engine: 'sqlite', formats };
}

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };
type Answer = (call: Call) => Response;

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const problem = (status: number, code: string) => json({ code, kind: 'config', status }, status);

function server(answers: Record<string, Answer | Answer[]> = {}) {
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
    // A list answers in turn and repeats its last entry.
    if (Array.isArray(answer)) {
      const next = answer.length > 1 ? answer.shift() : answer[0];
      if (next) return next(call);
    } else if (answer) return answer(call);
    if (call.url === '/api/profiles') return json(PROFILES);
    if (call.url === '/api/project/code-trust') return json(trust());
    if (call.url === '/api/connections' && call.method === 'POST') {
      return json({ connection_id: 'c1' }, 201);
    }
    if (call.url === '/api/connections/c1/migrations') {
      return json(migrations([{ format: 'sqlite_user_version', current: 7 }, alembic()]));
    }
    return new Response(null, { status: 204 });
  });
  return { fetcher: fetcher as unknown as typeof fetch, calls };
}

function writes(calls: Call[]) {
  return calls.filter((call) => call.method !== 'GET');
}

async function ready() {
  await screen.findByRole('option', { name: 'parts-dev (DEV)' });
}

describe('migration status view: code trust (T-16)', () => {
  it('STU-050: the status region shows no empty box before its first message', async () => {
    const user = userEvent.setup();
    const { fetcher } = server({
      'GET /api/project/code-trust': [
        () => json(trust()),
        () => json(trust({ state: 'trusted', trusted: true })),
      ],
    });
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    const status = screen.getByTestId('migrations-message');
    expect(status).toHaveAttribute('role', 'status');
    expect(status).toBeEmptyDOMElement();
    expect(status).toHaveClass('tb-sr-only');
    await user.click(await screen.findByRole('button', { name: 'Review files' }));
    await user.click(screen.getByRole('button', { name: 'Trust and run' }));
    await waitFor(() => expect(status).toHaveTextContent('The migration code is trusted.'));
    expect(status).not.toHaveClass('tb-sr-only');
  });

  it('opens the trust dialog on "Don\'t trust" and sends the digest it showed', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server({
      'GET /api/project/code-trust': [
        () => json(trust()),
        () => json(trust({ state: 'trusted', trusted: true })),
      ],
    });
    render(<MigrationsView fetcher={fetcher} />);
    expect(await screen.findByTestId('code-trust-state')).toHaveTextContent(
      "The project's Alembic files are not trusted.",
    );
    await user.click(screen.getByRole('button', { name: 'Review files' }));
    const dialog = screen.getByRole('dialog', { name: "Run the project's migration code?" });
    expect(dialog).toHaveAccessibleDescription(/runs their code on this computer/);
    expect(dialog).toHaveAccessibleDescription(
      /it gets the open connection's address and password/,
    );
    expect(within(dialog).getByRole('button', { name: "Don't trust" })).toHaveFocus();
    // The file names come from the project: each its own entry, escaped (U-1).
    const files = within(dialog).getByTestId('trust-code-files');
    expect(
      within(files)
        .getAllByRole('listitem')
        .map((item) => item.querySelector('code')?.textContent),
    ).toEqual(['0001_init.py', 'evil\\u202Eyp.py']);
    expect(within(dialog).getByTestId('trust-code-config')).toHaveTextContent('alembic.ini');
    expect(within(dialog).getByTestId('trust-code-config')).toHaveTextContent('migrations');
    // The project modules in the prepend_sys_path folders are covered too (F-7), and the dialog says so.
    expect(dialog).toHaveAccessibleDescription(/project modules in the prepend_sys_path folders/);
    const modules = within(dialog).getByTestId('trust-code-modules');
    expect(within(modules).getByRole('heading')).toHaveTextContent(
      'Project modules (prepend_sys_path) (2)',
    );
    expect(
      within(modules)
        .getAllByRole('listitem')
        .map((item) => item.querySelector('code')?.textContent),
    ).toEqual(['shop/models.py', 'shop/evil\\u202Eyp.py']);
    expect(dialog.textContent).not.toContain(BIDI);
    expect(dialog.textContent).not.toContain('server text');
    await user.click(within(dialog).getByRole('button', { name: 'Trust and run' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(writes(calls)).toEqual([
      expect.objectContaining({
        method: 'POST',
        url: '/api/project/code-trust',
        body: { digest: DIGEST },
      }),
    ]);
    expect(writes(calls)[0]?.headers['Content-Type']).toBe('application/json');
    expect(screen.getByTestId('migrations-message')).toHaveTextContent(
      'The migration code is trusted.',
    );
    expect(screen.getByTestId('code-trust-state')).toHaveTextContent(
      "You trust the project's Alembic files (2 revision files).",
    );
  });

  it('"Don\'t trust" sends nothing', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server();
    render(<MigrationsView fetcher={fetcher} />);
    await user.click(await screen.findByRole('button', { name: 'Review files' }));
    await user.click(screen.getByRole('button', { name: "Don't trust" }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Review files' }));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(writes(calls)).toEqual([]);
  });

  it('files changed meanwhile: the dialog stays open with the new list and sends the new digest', async () => {
    const user = userEvent.setup();
    const changed = trust({
      state: 'changed',
      digest: NEW_DIGEST,
      revision_files: ['0001_init.py', '0002_more.py'],
    });
    const { fetcher, calls } = server({
      'GET /api/project/code-trust': [() => json(trust()), () => json(changed)],
      'POST /api/project/code-trust': [
        () => problem(409, 'studio.trust_stale'),
        () => new Response(null, { status: 204 }),
      ],
    });
    render(<MigrationsView fetcher={fetcher} />);
    await user.click(await screen.findByRole('button', { name: 'Review files' }));
    await user.click(screen.getByRole('button', { name: 'Trust and run' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByTestId('trust-code-stale')).toHaveTextContent(
      'The files changed after they were shown.',
    );
    expect(within(dialog).getByTestId('trust-code-files')).toHaveTextContent('0002_more.py');
    await user.click(within(dialog).getByRole('button', { name: 'Trust and run' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(writes(calls).map((call) => call.body)).toEqual([
      { digest: DIGEST },
      { digest: NEW_DIGEST },
    ]);
  });

  it('a refused trust closes the dialog and says why', async () => {
    const user = userEvent.setup();
    const { fetcher } = server({
      'POST /api/project/code-trust': () => problem(409, 'studio.migrations_invalid'),
    });
    render(<MigrationsView fetcher={fetcher} />);
    await user.click(await screen.findByRole('button', { name: 'Review files' }));
    await user.click(screen.getByRole('button', { name: 'Trust and run' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByTestId('migrations-message').textContent).not.toBe('');
    expect(screen.getByTestId('migrations-message')).not.toHaveTextContent(
      'The migration code is trusted.',
    );
  });

  it('takes the trust back with a JSON DELETE', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server({
      'GET /api/project/code-trust': [
        () => json(trust({ state: 'trusted', trusted: true })),
        () => json(trust()),
      ],
    });
    render(<MigrationsView fetcher={fetcher} />);
    await user.click(await screen.findByRole('button', { name: 'Stop trusting' }));
    await waitFor(() =>
      expect(screen.getByTestId('migrations-message')).toHaveTextContent(
        'The migration code is no longer trusted.',
      ),
    );
    const [revoke] = writes(calls);
    expect(revoke).toEqual(
      expect.objectContaining({ method: 'DELETE', url: '/api/project/code-trust', body: {} }),
    );
    expect(revoke?.headers['Content-Type']).toBe('application/json');
    expect(screen.getByRole('button', { name: 'Review files' })).toBeVisible();
  });

  it('shows the states without a trust action: none, invalid (with the reason), unavailable', async () => {
    const none = server({
      'GET /api/project/code-trust': () =>
        json(
          trust({
            state: 'none',
            format: null,
            config_file: null,
            digest: null,
            revision_files: [],
          }),
        ),
    });
    const { unmount } = render(<MigrationsView fetcher={none.fetcher} />);
    expect(await screen.findByTestId('code-trust-state')).toHaveTextContent(
      'The project has no Alembic configuration',
    );
    expect(screen.queryByRole('button', { name: 'Review files' })).toBeNull();
    unmount();

    const invalid = server({
      'GET /api/project/code-trust': () =>
        json(trust({ state: 'invalid', digest: null, issue: 'migrations.location_outside' })),
    });
    const second = render(<MigrationsView fetcher={invalid.fetcher} />);
    await waitFor(() =>
      expect(screen.getByTestId('code-trust-state')).toHaveTextContent(
        'script_location lies outside the project folder.',
      ),
    );
    expect(screen.queryByRole('button', { name: 'Review files' })).toBeNull();
    second.unmount();

    const odd = server({
      'GET /api/project/code-trust': () =>
        json(trust({ state: 'invalid', digest: null, issue: `config.x${BIDI}` })),
    });
    const third = render(<MigrationsView fetcher={odd.fetcher} />);
    await waitFor(() =>
      expect(screen.getByTestId('code-trust-state')).toHaveTextContent(
        'the check failed (config.x\\u202E).',
      ),
    );
    third.unmount();

    const named = server({
      'GET /api/project/code-trust': () =>
        json(
          trust({
            state: 'invalid',
            digest: null,
            issue: 'migrations.modules_too_large',
            issue_file: `shop/big${BIDI}.py`,
          }),
        ),
    });
    const fourth = render(<MigrationsView fetcher={named.fetcher} />);
    await waitFor(() =>
      expect(screen.getByTestId('code-trust-state')).toHaveTextContent(
        'are larger than Studio checks. The file: shop/big\\u202E.py',
      ),
    );
    fourth.unmount();

    const down = server({ 'GET /api/project/code-trust': () => problem(500, 'studio.internal') });
    render(<MigrationsView fetcher={down.fetcher} />);
    await waitFor(() =>
      expect(screen.getByTestId('code-trust-state')).toHaveTextContent(
        'Studio could not read the trust state',
      ),
    );
  });
});

describe('migration status view: status', () => {
  it('opens the read-only connection and shows each format, escaped', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server();
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    const sqlite = await screen.findByTestId('migrations-sqlite');
    expect(sqlite).toHaveTextContent('7');
    const section = screen.getByTestId('migrations-alembic');
    expect(within(section).getByTestId('migrations-summary')).toHaveTextContent(
      "the project's migration code is not trusted",
    );
    expect(section).toHaveTextContent('rev\\u202E1');
    expect(section.textContent).not.toContain(BIDI);
    expect(writes(calls)).toEqual([
      expect.objectContaining({
        method: 'POST',
        url: '/api/connections',
        body: { profile_id: 'project:parts-dev', confirmed_environment: 'dev' },
      }),
    ]);
    expect(calls.some((call) => call.url === '/api/connections/c1/migrations')).toBe(true);
  });

  it('asks D-02 before it opens a PROD connection; Cancel opens nothing', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server();
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    let dialog = screen.getByRole('dialog', { name: 'Production database' });
    expect(dialog).toHaveAccessibleDescription(/measure\\u202E is marked as PROD/);
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(writes(calls)).toEqual([]);
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    dialog = screen.getByRole('dialog', { name: 'Production database' });
    await user.click(within(dialog).getByRole('button', { name: 'Connect' }));
    await screen.findByTestId('migrations-alembic');
    expect(writes(calls)).toEqual([
      expect.objectContaining({
        body: { profile_id: 'user:measure', confirmed_environment: 'prod' },
      }),
    ]);
  });

  it('shows pending revisions, revisions the project does not know, and up to date', async () => {
    const user = userEvent.setup();
    const read = alembic({
      code_trust: 'trusted',
      current: ['0001'],
      heads: ['0003'],
      pending: ['0002', '0003'],
      is_current: false,
      unknown: [`ghost${BIDI}`],
      revisions: 'read',
    });
    const { fetcher } = server({
      'GET /api/connections/c1/migrations': [
        () => json(migrations([read])),
        () =>
          json(
            migrations([
              alembic({
                code_trust: 'trusted',
                current: ['0003'],
                heads: ['0003'],
                pending: [],
                is_current: true,
                unknown: [],
                revisions: 'read',
              }),
            ]),
          ),
      ],
    });
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    expect(await screen.findByTestId('migrations-summary')).toHaveTextContent(
      '2 revisions are not yet applied.',
    );
    expect(screen.getByTestId('migrations-alembic')).toHaveTextContent('0002');
    expect(screen.getByTestId('migrations-unknown')).toHaveTextContent('ghost\\u202E');
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    await waitFor(() =>
      expect(screen.getByTestId('migrations-summary')).toHaveTextContent(
        'The database is up to date.',
      ),
    );
    expect(screen.queryByTestId('migrations-unknown')).toBeNull();
  });

  it('TD-046 B-3: pending revisions get the upgrade command with Copy; none when up to date', async () => {
    const user = userEvent.setup();
    const pending = (heads: string[]) =>
      alembic({
        code_trust: 'trusted',
        current: ['0001'],
        heads,
        pending: ['0002'],
        is_current: false,
        unknown: [],
        revisions: 'read',
      });
    const { fetcher, calls } = server({
      'GET /api/connections/c1/migrations': [
        () => json(migrations([pending(['0002'])])),
        () => json(migrations([pending(['0002a', '0002b'])])),
        () =>
          json(
            migrations([
              alembic({
                code_trust: 'trusted',
                current: ['0002'],
                heads: ['0002'],
                pending: [],
                is_current: true,
                unknown: [],
                revisions: 'read',
              }),
            ]),
          ),
      ],
    });
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    const command = await screen.findByTestId('migrations-command');
    expect(command).toHaveTextContent('Studio does not run migrations.');
    expect(within(command).getByTestId('migrations-command-text')).toHaveTextContent(
      /^alembic upgrade head$/,
    );
    // After userEvent.setup(), which puts a clipboard stub of its own on navigator.
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    await user.click(within(command).getByRole('button', { name: 'Copy command' }));
    expect(writeText).toHaveBeenCalledWith('alembic upgrade head');
    await waitFor(() =>
      expect(screen.getByTestId('migrations-command-status')).toHaveTextContent(
        'The command is on the clipboard.',
      ),
    );
    // Copying is not a request: the only write is the connection the status opened.
    expect(writes(calls).map((call) => `${call.method} ${call.url}`)).toEqual([
      'POST /api/connections',
    ]);
    // Two heads: "heads".
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    await waitFor(() =>
      expect(screen.getByTestId('migrations-command-text')).toHaveTextContent(
        /^alembic upgrade heads$/,
      ),
    );
    // A refused copy says so.
    writeText.mockRejectedValueOnce(new Error('denied'));
    await user.click(screen.getByRole('button', { name: 'Copy command' }));
    await waitFor(() =>
      expect(screen.getByTestId('migrations-command-status')).toHaveTextContent(
        'The command could not be copied.',
      ),
    );
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    await waitFor(() =>
      expect(screen.getByTestId('migrations-summary')).toHaveTextContent(
        'The database is up to date.',
      ),
    );
    expect(screen.queryByTestId('migrations-command')).toBeNull();
  });

  it('shows the problem of the revisions and of opening the connection', async () => {
    const user = userEvent.setup();
    const busy = alembic({
      code_trust: 'trusted',
      revisions: 'unknown (could not be read)',
      problem: { code: 'studio.migrations_busy', kind: 'database', status: 429 },
    });
    const { fetcher } = server({
      'GET /api/connections/c1/migrations': () => json(migrations([busy])),
      'POST /api/connections': [
        () => json({ connection_id: 'c1' }, 201),
        () => problem(502, 'studio.connection_failed'),
      ],
    });
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    expect(await screen.findByTestId('migrations-summary')).toHaveTextContent(
      'Studio is already reading the revisions for another migration status.',
    );
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Studio could not reach the database.',
    );
    expect(screen.queryByTestId('migrations-alembic')).toBeNull();
  });

  it('says so without profiles or without a format', async () => {
    const user = userEvent.setup();
    const empty = server({
      'GET /api/profiles': () => json({ ...PROFILES, profiles: [] }),
    });
    const { unmount } = render(<MigrationsView fetcher={empty.fetcher} />);
    expect(await screen.findByText(/There are no connection profiles yet/)).toBeVisible();
    unmount();
    const nothing = server({
      'GET /api/connections/c1/migrations': () => json(migrations([])),
    });
    render(<MigrationsView fetcher={nothing.fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    expect(
      await screen.findByText('Studio found no migration format for this database.'),
    ).toBeVisible();
  });
});

describe('migration API parsing', () => {
  it('fails closed on unexpected values', () => {
    expect(parseCodeTrust(null)).toBeNull();
    expect(parseCodeTrust({ state: 'root' })).toBeNull();
    expect(parseCodeTrust({ state: 'trusted', revision_files: [1, 'a.py'] })).toEqual({
      state: 'trusted',
      configFile: null,
      digest: null,
      scriptLocation: null,
      revisionFiles: ['a.py'],
      moduleFiles: [],
      issue: null,
      issueFile: null,
    });
    expect(parseCodeTrust({ state: 'trusted', module_files: ['m.py', null] })?.moduleFiles).toEqual(
      ['m.py'],
    );
    expect(parseMigrations('x')).toBeNull();
    const parsed = parseMigrations({
      connection_id: 'c1',
      engine: 'sqlite',
      formats: [null, { format: 'flyway' }, { format: 'sqlite_user_version', current: '7' }],
    });
    expect(parsed?.formats).toEqual([
      { format: 'other', name: 'flyway' },
      { format: 'sqlite_user_version', current: null },
    ]);
    const levuvo = parseMigrations({
      formats: [
        { format: 'orkexa', schema: 's', table: 't', current: 'x', state: 'boom', steps: [3] },
      ],
    });
    expect(levuvo?.formats).toEqual([
      {
        format: 'orkexa',
        table: 's.t',
        namespace: null,
        current: null,
        state: 'unreadable',
        steps: [],
        problem: null,
      },
    ]);
  });
});

describe('migration status view: review fixes of #32', () => {
  it('C-1: shows the Levuvo version tables, escaped, and counts formats it cannot show', async () => {
    const user = userEvent.setup();
    const { fetcher } = server({
      'GET /api/connections/c1/migrations': () =>
        json({
          connection_id: 'c1',
          engine: 'postgresql',
          formats: [
            {
              format: 'nodestro',
              schema: 'public',
              table: `nodestro_schema_migrations${BIDI}`,
              current: 1,
              state: 'pending',
              files: 'nodestro_runtime/migrations/postgres',
              files_issue: null,
              steps: [
                { version: 1, name: '0001_init.sql', status: 'applied' },
                {
                  version: 2,
                  name: `0002_x${BIDI}.sql`,
                  status: 'pending',
                  preview: 'DROP TABLE x',
                },
              ],
              problem: null,
            },
            {
              format: 'axentra',
              schema: 'public',
              table: 'axentra_schema_migrations',
              namespace: 'axentra',
              current: null,
              state: 'unreadable',
              steps: [],
              problem: { code: 'studio.connection_failed', kind: 'database', status: 502 },
            },
            { format: 'flyway' },
          ],
        }),
    });
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    const [nodestro, axentra] = await screen.findAllByTestId('migrations-levuvo');
    expect(nodestro).toHaveTextContent('Nodestro public.nodestro_schema_migrations\\u202E');
    expect(nodestro).toHaveTextContent('The files have steps the database has not applied.');
    expect(nodestro).toHaveTextContent('2 0002_x\\u202E.sql (not yet applied)');
    expect(nodestro).not.toHaveTextContent('0001_init.sql');
    expect(nodestro?.textContent).not.toContain(BIDI);
    expect(nodestro?.textContent).not.toContain('DROP TABLE');
    expect(axentra).toHaveTextContent('Studio could not read this version table.');
    expect(axentra).toHaveTextContent('Studio could not reach the database.');
    expect(screen.getByTestId('migrations-others')).toHaveTextContent(
      '1 further migration format was found but is not shown here yet.',
    );
    expect(screen.queryByText('Studio found no migration format for this database.')).toBeNull();
  });

  it('L-1: after trusting, reads the shown status again on its connection without opening one', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server();
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    await screen.findByTestId('migrations-alembic');
    const reads = () =>
      calls.filter((call) => call.url === '/api/connections/c1/migrations').length;
    expect(reads()).toBe(1);
    await user.click(screen.getByRole('button', { name: 'Review files' }));
    await user.click(screen.getByRole('button', { name: 'Trust and run' }));
    await waitFor(() => expect(reads()).toBe(2));
    expect(calls.filter((call) => call.url === '/api/connections').length).toBe(1);
  });

  it('L-2: escapes profile names in the selector', async () => {
    const { fetcher } = server();
    render(<MigrationsView fetcher={fetcher} />);
    const option = await screen.findByRole('option', { name: 'measure\\u202E (PROD)' });
    expect(option.textContent).not.toContain(BIDI);
  });

  it('L-3: marks the environment of the shown status', async () => {
    const user = userEvent.setup();
    const { fetcher } = server();
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    await user.click(screen.getByRole('button', { name: 'Connect' }));
    await screen.findByTestId('migrations-alembic');
    expect(
      within(screen.getByTestId('migrations-result')).getByTestId('env-badge'),
    ).toHaveAttribute('data-env', 'prod');
    expect(screen.getByTestId('migrations-result')).toHaveAttribute('aria-live', 'polite');
  });

  it('L-4: stale, then a state that cannot be trusted: the dialog closes and says why', async () => {
    const user = userEvent.setup();
    const { fetcher } = server({
      'GET /api/project/code-trust': [
        () => json(trust()),
        () => json(trust({ state: 'invalid', digest: null, issue: 'migrations.file_name' })),
      ],
      'POST /api/project/code-trust': () => problem(409, 'studio.trust_stale'),
    });
    render(<MigrationsView fetcher={fetcher} />);
    await user.click(await screen.findByRole('button', { name: 'Review files' }));
    await user.click(screen.getByRole('button', { name: 'Trust and run' }));
    await waitFor(() =>
      expect(screen.getByTestId('migrations-message')).toHaveTextContent(
        'The files changed after they were shown.',
      ),
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('L-6: after taking the trust back, reads the shown status again without opening a connection', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server({
      'GET /api/project/code-trust': [
        () => json(trust({ state: 'trusted', trusted: true })),
        () => json(trust()),
      ],
    });
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    await screen.findByTestId('migrations-alembic');
    const reads = () =>
      calls.filter((call) => call.url === '/api/connections/c1/migrations').length;
    expect(reads()).toBe(1);
    await user.click(screen.getByRole('button', { name: 'Stop trusting' }));
    await waitFor(() => expect(reads()).toBe(2));
    expect(calls.filter((call) => call.url === '/api/connections').length).toBe(1);
  });

  it('L-9: drops a status that arrives after the profile was switched', async () => {
    const user = userEvent.setup();
    let answer: (response: Response) => void = () => undefined;
    const late = new Promise<Response>((resolve) => {
      answer = resolve;
    });
    const { fetcher } = server({
      'GET /api/connections/c1/migrations': (() => late) as unknown as Answer,
    });
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    expect(await screen.findByRole('button', { name: 'Reading…' })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    expect(screen.getByRole('button', { name: 'Show status' })).toBeEnabled();
    answer(json(migrations([{ format: 'sqlite_user_version', current: 7 }])));
    await late;
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByTestId('migrations-sqlite')).toBeNull();
    expect(screen.getByTestId('migrations-result')).toBeEmptyDOMElement();
  });

  function deferred() {
    let resolve: (response: Response) => void = () => undefined;
    const promise = new Promise<Response>((done) => {
      resolve = done;
    });
    return { promise, resolve, answer: (() => promise) as unknown as Answer };
  }

  const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

  it('F-1: no reread after a revoke when the profile was switched meanwhile', async () => {
    const user = userEvent.setup();
    const revoked = deferred();
    const { fetcher, calls } = server({
      'GET /api/project/code-trust': [
        () => json(trust({ state: 'trusted', trusted: true })),
        () => json(trust()),
      ],
      'DELETE /api/project/code-trust': revoked.answer,
    });
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    await screen.findByTestId('migrations-sqlite');
    await user.click(screen.getByRole('button', { name: 'Stop trusting' }));
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    revoked.resolve(new Response(null, { status: 204 }));
    await waitFor(() =>
      expect(screen.getByTestId('migrations-message')).toHaveTextContent(
        'The migration code is no longer trusted.',
      ),
    );
    await settle();
    expect(calls.filter((call) => call.url === '/api/connections/c1/migrations')).toHaveLength(1);
    expect(screen.getByTestId('migrations-result')).toBeEmptyDOMElement();
  });

  it('F-1: no reread after a trust when the profile was switched meanwhile', async () => {
    const user = userEvent.setup();
    const trusted = deferred();
    const { fetcher, calls } = server({ 'POST /api/project/code-trust': trusted.answer });
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    await screen.findByTestId('migrations-sqlite');
    await user.click(screen.getByRole('button', { name: 'Review files' }));
    await user.click(screen.getByRole('button', { name: 'Trust and run' }));
    // The dialog is modal in a browser; jsdom lets the test switch behind it to reach the race.
    fireEvent.change(screen.getByLabelText('Connection profile'), {
      target: { value: 'user:measure' },
    });
    trusted.resolve(new Response(null, { status: 204 }));
    await waitFor(() =>
      expect(screen.getByTestId('migrations-message')).toHaveTextContent(
        'The migration code is trusted.',
      ),
    );
    await settle();
    expect(calls.filter((call) => call.url === '/api/connections/c1/migrations')).toHaveLength(1);
    expect(screen.getByTestId('migrations-result')).toBeEmptyDOMElement();
  });

  it('F-1: drops a reread that a profile switch overtook', async () => {
    const user = userEvent.setup();
    const late = deferred();
    const { fetcher } = server({
      'GET /api/project/code-trust': [
        () => json(trust({ state: 'trusted', trusted: true })),
        () => json(trust()),
      ],
      'GET /api/connections/c1/migrations': [
        () => json(migrations([{ format: 'sqlite_user_version', current: 7 }])),
        late.answer,
      ],
    });
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    await screen.findByTestId('migrations-sqlite');
    await user.click(screen.getByRole('button', { name: 'Stop trusting' }));
    expect(await screen.findByRole('button', { name: 'Reading…' })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    late.resolve(json(migrations([{ format: 'sqlite_user_version', current: 8 }])));
    await late.promise;
    await settle();
    expect(screen.getByTestId('migrations-result')).toBeEmptyDOMElement();
  });

  it('F-1: a later reread wins over an earlier one that answers late', async () => {
    const user = userEvent.setup();
    const late = deferred();
    const { fetcher } = server({
      'GET /api/project/code-trust': [
        () => json(trust({ state: 'trusted', trusted: true })),
        () => json(trust()),
        () => json(trust({ state: 'trusted', trusted: true })),
      ],
      'GET /api/connections/c1/migrations': [
        () => json(migrations([{ format: 'sqlite_user_version', current: 7 }])),
        late.answer,
        () => json(migrations([{ format: 'sqlite_user_version', current: 9 }])),
      ],
    });
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    await screen.findByTestId('migrations-sqlite');
    await user.click(screen.getByRole('button', { name: 'Stop trusting' }));
    await user.click(await screen.findByRole('button', { name: 'Review files' }));
    await user.click(screen.getByRole('button', { name: 'Trust and run' }));
    await waitFor(() => expect(screen.getByTestId('migrations-sqlite')).toHaveTextContent('9'));
    late.resolve(json(migrations([{ format: 'sqlite_user_version', current: 8 }])));
    await late.promise;
    await settle();
    expect(screen.getByTestId('migrations-sqlite')).toHaveTextContent('9');
    expect(screen.getByTestId('migrations-sqlite')).not.toHaveTextContent('8');
  });
});

describe('migration status view: the STU-024 formats as #30 sends them', () => {
  function levuvo(extra: Record<string, unknown>) {
    return {
      format: 'nodestro',
      schema: 'public',
      table: 'nodestro_schema_migrations',
      namespace: null,
      current: null,
      state: 'unreadable',
      files: null,
      files_issue: null,
      steps: [],
      problem: null,
      ...extra,
    };
  }

  async function showFormats(formats: unknown[]) {
    const user = userEvent.setup();
    const { fetcher } = server({
      'GET /api/connections/c1/migrations': () =>
        json({ connection_id: 'c1', engine: 'postgresql', formats }),
    });
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    return screen.findAllByTestId('migrations-levuvo');
  }

  it('L-8: names why a version table could not be read', async () => {
    const reason = (code: string, kind: string, status: number) =>
      levuvo({ problem: { code, kind, status } });
    const [permission, table, refused, timeout, other] = await showFormats([
      reason('db.permission', 'permission', 403),
      reason('studio.migrations_table', 'config', 422),
      reason('studio.statement_refused', 'request', 400),
      reason('db.timeout', 'timeout', 504),
      reason('db.connection', 'connection', 502),
    ]);
    expect(permission).toHaveTextContent(
      "Studio could not read this version table. The connection's role may not read it",
    );
    expect(table).toHaveTextContent('A column Studio reads is missing or has an unexpected type');
    expect(refused).toHaveTextContent('Studio refused its own read of the table');
    expect(timeout).toHaveTextContent('Reading it took longer than the statement timeout.');
    // Any other code: the page's general text for it (STU-008 gave db.connection a text that says what to check).
    expect(other).toHaveTextContent(
      'Studio could not read this version table. Studio could not connect.',
    );
    expect(other).not.toHaveTextContent('version table. The request failed');
  });

  it('shows every format #30 knows, each state in its own words, and an entry from files only', async () => {
    const entries = await showFormats([
      levuvo({ state: 'current', current: 3 }),
      levuvo({ format: 'axentra', state: 'newer', current: 9, namespace: 'axentra' }),
      levuvo({ format: 'orkexa', state: 'changed', current: 2 }),
      levuvo({ state: 'no_files', current: 1 }),
      levuvo({ format: 'orkexa', state: 'unknown_state', current: 4 }),
      levuvo({
        format: 'axentra',
        schema: null,
        table: null,
        state: 'pending',
        steps: [{ version: 1, name: 'init', status: 'pending', preview: 'CREATE TABLE t' }],
      }),
    ]);
    expect(entries).toHaveLength(6);
    expect(entries[0]).toHaveTextContent('The database is up to date with the files.');
    expect(entries[0]).toHaveTextContent('3');
    expect(entries[1]).toHaveTextContent('Axentra public.nodestro_schema_migrations / axentra');
    expect(entries[1]).toHaveTextContent('The database has versions that the files do not know.');
    expect(entries[2]).toHaveTextContent('Orkexa');
    expect(entries[2]).toHaveTextContent('An applied file was changed after it ran.');
    expect(entries[3]).toHaveTextContent('Studio found no files for this table');
    // An unknown state reads as unreadable with no problem: the plain text, never an empty line (F-3, M11).
    expect(
      within(entries[4] as HTMLElement).getByTestId('migrations-levuvo-state').textContent,
    ).toBe('Studio could not read this version table.');
    expect(within(entries[5] as HTMLElement).getByRole('heading')).toHaveTextContent(/^Axentra$/);
    expect(entries[5]).toHaveTextContent('1 init (not yet applied)');
    expect(entries[5]?.textContent).not.toContain('CREATE TABLE');
    expect(screen.queryByTestId('migrations-levuvo-more')).toBeNull();
  });

  it('L-7, I-6: escapes an unknown step status and caps the steps and long names', async () => {
    const steps = Array.from({ length: MAX_LEVUVO_STEPS + 5 }, (_, index) => ({
      version: index + 1,
      name: index === 0 ? `${'n'.repeat(200)}${BIDI}` : `step${index + 1}`,
      status: index === 1 ? `odd${BIDI}` : index === 2 ? 'x'.repeat(100) : 'pending',
    }));
    const [entry] = await showFormats([levuvo({ state: 'pending', current: 0, steps })]);
    const items = within(entry as HTMLElement).getAllByRole('listitem');
    expect(items).toHaveLength(MAX_LEVUVO_STEPS);
    expect(items[0]?.textContent).not.toContain(BIDI);
    expect(items[0]?.textContent).toContain('…');
    expect(items[0]?.textContent?.length).toBeLessThan(120);
    expect(items[1]).toHaveTextContent('2 step2 (odd\\u202E)');
    expect(items[1]?.textContent).not.toContain(BIDI);
    expect(screen.getByTestId('migrations-levuvo-more')).toHaveTextContent('and 5 more steps');
    expect(items[2]).toHaveTextContent(`3 step3 (${'x'.repeat(64)}…)`);
  });
});

describe('migration status view: the step cap boundary', () => {
  it('F-3 (M6): exactly MAX_LEVUVO_STEPS open steps show no "and N more" line', async () => {
    const user = userEvent.setup();
    const steps = Array.from({ length: MAX_LEVUVO_STEPS }, (_, index) => ({
      version: index + 1,
      name: `s${index + 1}`,
      status: 'pending',
    }));
    const { fetcher } = server({
      'GET /api/connections/c1/migrations': () =>
        json({
          connection_id: 'c1',
          engine: 'postgresql',
          formats: [{ format: 'nodestro', table: 't', state: 'pending', current: 0, steps }],
        }),
    });
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    const entry = await screen.findByTestId('migrations-levuvo');
    expect(within(entry).getAllByRole('listitem')).toHaveLength(MAX_LEVUVO_STEPS);
    expect(screen.queryByTestId('migrations-levuvo-more')).toBeNull();
  });
});

describe('migrations view: D-02 on the server (TD-038)', () => {
  it('a profile that became PROD after the page read it: the refusal reads the profiles again, the next try asks D-02', async () => {
    const user = userEvent.setup();
    const flipped = {
      ...PROFILES,
      profiles: [{ ...PROFILES.profiles[0], environment: 'prod' }, PROFILES.profiles[1]],
    };
    const { fetcher, calls } = server({
      'GET /api/profiles': [() => json(PROFILES), () => json(flipped)],
      'POST /api/connections': [
        () =>
          json({ code: 'studio.prod_confirmation_required', kind: 'database', status: 409 }, 409),
        () => json({ connection_id: 'c1' }, 201),
      ],
    });
    render(<MigrationsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    expect(await screen.findByText(/This profile is marked as PROD now/)).toBeVisible();
    await screen.findByRole('option', { name: 'parts-dev (PROD)' });
    await user.click(screen.getByRole('button', { name: 'Show status' }));
    const dialog = screen.getByRole('dialog', { name: 'Production database' });
    await user.click(within(dialog).getByRole('button', { name: 'Connect' }));
    await waitFor(() =>
      expect(
        writes(calls)
          .filter((call) => call.url === '/api/connections')
          .map((call) => call.body),
      ).toEqual([
        { profile_id: 'project:parts-dev', confirmed_environment: 'dev' },
        { profile_id: 'project:parts-dev', confirmed_environment: 'prod' },
      ]),
    );
  });
});
