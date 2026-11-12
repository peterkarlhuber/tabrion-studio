/**
 * STU-014: the diagnostics view. "Run diagnosis" opens the profile's read-only connection (D-02 first for PROD) and
 * posts the diagnosis as JSON; the findings show severity (icon and word), code, title, detail, remedy and source,
 * every server text escaped and capped (U-1). Cancel posts the cancel route and the incomplete result says so; a
 * profile switch or leaving the page cancels a running diagnosis, and its late answer is dropped. The parser fails
 * closed: an unknown severity stays visible and is counted on its own, a broken answer shows nothing of it.
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { countFindings, parseDiagnosis } from '../../api/diagnostics';
import i18n from '../../i18n';
import {
  DiagnosticsView,
  MAX_CODE_LENGTH,
  MAX_FINDINGS,
  MAX_TEXT_LENGTH,
  MAX_TITLE_LENGTH,
} from './DiagnosticsView';

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

function finding(extra: Record<string, unknown> = {}) {
  return {
    code: 'TAB-SQ-003',
    severity: 'warning',
    title: 'Database file is readable by others',
    detail: 'The file mode allows other users to read it.',
    remedy: 'chmod 600 the file.',
    source: 'tabrion',
    ...extra,
  };
}

const OWNER = finding({
  code: 'STU-DX-006',
  severity: 'error',
  title: 'Role owns tables',
  detail: 'The role owns 3 table(s).',
  remedy: 'Use an owner role for migrations.',
  source: 'studio',
});
const HOSTILE = finding({
  code: 'TAB-PG-002',
  title: `Schema audit${BIDI}gol is writable`,
  detail: `Schema audit${BIDI}gol grants CREATE to PUBLIC. <img src=x onerror="alert(1)">`,
  remedy: `REVOKE CREATE ON SCHEMA audit${BIDI}gol FROM PUBLIC;\nDROP TABLE x`,
});
const NOTE = finding({
  code: 'STU-DX-010',
  severity: 'info',
  title: 'Session settings missing for the role or database',
  detail: 'Neither the role nor the database sets lock_timeout.',
  remedy: 'ALTER ROLE ... SET lock_timeout.',
  source: 'studio',
});
const INCOMPLETE = finding({
  code: 'STU-DX-008',
  severity: 'info',
  title: 'Not every check ran',
  detail: 'The diagnosis stopped because you canceled it. Not run: STU-DX-004.',
  remedy: 'Run the diagnosis again.',
  source: 'studio',
});

function diagnosis(findings: unknown[], extra: Record<string, unknown> = {}) {
  return {
    connection_id: 'c1',
    engine: 'postgresql',
    environment: 'dev',
    ran_at: 1_794_500_000.5,
    findings,
    counts: { error: 0, warning: 0, info: 0 },
    ...extra,
  };
}

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };
type Answer = (call: Call) => Response | Promise<Response>;

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
    if (call.url === '/api/connections' && call.method === 'POST') {
      return json({ connection_id: 'c1' }, 201);
    }
    if (call.url === '/api/connections/c1/diagnostics') {
      return json(diagnosis([OWNER, HOSTILE, NOTE]));
    }
    if (call.url === '/api/connections/c1/diagnostics/cancel') {
      return json({ canceled: true }, 202);
    }
    return new Response(null, { status: 204 });
  });
  return { fetcher: fetcher as unknown as typeof fetch, calls };
}

function writes(calls: Call[]) {
  return calls.filter((call) => call.method !== 'GET');
}

function deferred() {
  let resolve: (response: Response) => void = () => undefined;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve, answer: (() => promise) as Answer };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

async function ready() {
  await screen.findByRole('option', { name: 'parts-dev (DEV)' });
}

/** The item at `index`; fails the test when there is none. */
function nth(items: HTMLElement[], index: number): HTMLElement {
  const item = items[index];
  if (item === undefined) throw new Error(`no item ${index}`);
  return item;
}

function findings() {
  return within(screen.getByTestId('diagnostics-findings')).getAllByTestId('diagnostics-finding');
}

afterEach(async () => {
  await act(async () => {
    await i18n.changeLanguage('en');
  });
});

describe('diagnostics view: run', () => {
  it('opens the read-only connection, posts the diagnosis as JSON and lists the findings', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server();
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    await screen.findByTestId('diagnostics-findings');
    expect(writes(calls).map((call) => [call.method, call.url, call.body])).toEqual([
      [
        'POST',
        '/api/connections',
        { profile_id: 'project:parts-dev', confirmed_environment: 'dev' },
      ],
      ['POST', '/api/connections/c1/diagnostics', {}],
    ]);
    for (const call of writes(calls)) expect(call.headers['Content-Type']).toBe('application/json');
    expect(screen.getByTestId('diagnostics-summary')).toHaveTextContent(
      '1 error · 1 warning · 1 note',
    );
    expect(screen.getByTestId('diagnostics-message')).toHaveTextContent(
      'Diagnosis finished: 1 error · 1 warning · 1 note',
    );
    // It repeats the summary: announced, not shown twice.
    expect(screen.getByTestId('diagnostics-message')).toHaveClass('tb-sr-only');
    const items = findings();
    expect(items.map((item) => item.dataset.severity)).toEqual(['error', 'warning', 'info']);
    // Severity in words (the icon is decoration), code, title as the item's name, source.
    expect(within(nth(items, 0)).getByTestId('diagnostics-severity')).toHaveTextContent('Error');
    expect(within(nth(items, 0)).getByTestId('diagnostics-code')).toHaveTextContent('STU-DX-006');
    expect(items[0]).toHaveAccessibleName('Role owns tables');
    expect(items[0]).toHaveTextContent('from Studio');
    expect(within(nth(items, 0)).getByTestId('diagnostics-remedy')).toHaveTextContent(
      'Remedy: Use an owner role for migrations.',
    );
    expect(items[1]).toHaveTextContent('from tabrion');
    expect(items[2]).toHaveTextContent('Note');
    expect(screen.getByTestId('diagnostics-diagnosis')).toHaveTextContent('parts-dev');
    expect(screen.getByTestId('env-badge')).toHaveAttribute('data-env', 'dev');
    const time = screen.getByTestId('diagnostics-diagnosis').querySelector('time');
    expect(time).toHaveAttribute('dateTime', new Date(1_794_500_000_500).toISOString());
    expect(screen.queryByTestId('diagnostics-incomplete')).toBeNull();
    expect(screen.queryByTestId('diagnostics-more')).toBeNull();
  });

  it('escapes the server texts: bidi controls, newlines and markup stay visible text (U-1)', async () => {
    const user = userEvent.setup();
    const { fetcher } = server();
    const { container } = render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    const item = nth(await screen.findAllByTestId('diagnostics-finding'), 1);
    expect(item).toHaveAccessibleName('Schema audit\\u202Egol is writable');
    expect(within(item).getByTestId('diagnostics-detail')).toHaveTextContent(
      'Schema audit\\u202Egol grants CREATE to PUBLIC. <img src=x onerror="alert(1)">',
    );
    expect(within(item).getByTestId('diagnostics-remedy')).toHaveTextContent(
      'REVOKE CREATE ON SCHEMA audit\\u202Egol FROM PUBLIC;\\u000ADROP TABLE x',
    );
    expect(container.textContent).not.toContain(BIDI);
    expect(container.querySelector('img')).toBeNull();
    // The server's text is English, also on a German page.
    expect(within(item).getByTestId('diagnostics-detail')).toHaveAttribute('lang', 'en');
  });

  it('says so when there is no finding', async () => {
    const user = userEvent.setup();
    const { fetcher } = server({
      'POST /api/connections/c1/diagnostics': () => json(diagnosis([])),
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    expect(await screen.findByTestId('diagnostics-summary')).toHaveTextContent(
      'No findings: the checks found nothing to report.',
    );
    expect(screen.queryByTestId('diagnostics-findings')).toBeNull();
  });

  it('asks D-02 before it opens a PROD connection; Cancel opens nothing', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server({
      'POST /api/connections/c1/diagnostics': () =>
        json(diagnosis([OWNER], { environment: 'prod' })),
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    let dialog = screen.getByRole('dialog', { name: 'Production database' });
    expect(dialog).toHaveAccessibleDescription(/measure\\u202E is marked as PROD/);
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(writes(calls)).toEqual([]);
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    dialog = screen.getByRole('dialog', { name: 'Production database' });
    await user.click(within(dialog).getByRole('button', { name: 'Connect' }));
    await screen.findByTestId('diagnostics-findings');
    expect(writes(calls).map((call) => call.url)).toEqual([
      '/api/connections',
      '/api/connections/c1/diagnostics',
    ]);
    expect(writes(calls)[0]?.body).toEqual({
      profile_id: 'user:measure',
      confirmed_environment: 'prod',
    });
    expect(screen.getByTestId('env-badge')).toHaveAttribute('data-env', 'prod');
  });

  it('PROD wins: a server that says prod for a DEV profile gets the PROD badge', async () => {
    const user = userEvent.setup();
    const { fetcher } = server({
      'POST /api/connections/c1/diagnostics': () =>
        json(diagnosis([OWNER], { environment: 'prod' })),
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    await screen.findByTestId('diagnostics-findings');
    expect(screen.getByTestId('env-badge')).toHaveAttribute('data-env', 'prod');
  });

  it('PROD wins: a PROD profile keeps its badge when the server names another environment', async () => {
    const user = userEvent.setup();
    const { fetcher } = server({
      'POST /api/connections/c1/diagnostics': () =>
        json(diagnosis([OWNER], { environment: 'dev' })),
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    const dialog = screen.getByRole('dialog', { name: 'Production database' });
    await user.click(within(dialog).getByRole('button', { name: 'Connect' }));
    await screen.findByTestId('diagnostics-findings');
    expect(screen.getByTestId('env-badge')).toHaveAttribute('data-env', 'prod');
  });

  it('"Run again" uses the shown connection and never opens one (D-02 cannot be skipped)', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server({
      // The answer names another connection id: "Run again" keeps the one the page opened.
      'POST /api/connections/c1/diagnostics': [
        () => json(diagnosis([OWNER], { connection_id: 'other' })),
        () => json(diagnosis([])),
      ],
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    await screen.findByTestId('diagnostics-findings');
    await user.click(screen.getByRole('button', { name: 'Run again' }));
    expect(await screen.findByTestId('diagnostics-summary')).toHaveTextContent('No findings');
    expect(writes(calls).map((call) => call.url)).toEqual([
      '/api/connections',
      '/api/connections/c1/diagnostics',
      '/api/connections/c1/diagnostics',
    ]);
  });

  it('says so without profiles', async () => {
    const { fetcher } = server({
      'GET /api/profiles': () => json({ ...PROFILES, profiles: [] }),
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    expect(
      await screen.findByText('There are no connection profiles yet. Add one under Connections.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Run diagnosis' })).toBeNull();
  });
});

describe('diagnostics view: problems', () => {
  it('shows why the connection did not open, and runs nothing', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server({
      'POST /api/connections': () => problem(502, 'studio.connection_failed'),
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Studio could not reach the database.',
    );
    expect(calls.some((call) => call.url.endsWith('/diagnostics'))).toBe(false);
    expect(screen.getByRole('button', { name: 'Run diagnosis' })).toHaveAttribute(
      'aria-disabled',
      'false',
    );
  });

  it("shows a refused diagnosis in the page's words, never the server's detail", async () => {
    const user = userEvent.setup();
    const { fetcher } = server({
      'POST /api/connections/c1/diagnostics': () =>
        json({ code: 'studio.connection_not_found', detail: 'server words', status: 404 }, 404),
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The connection is no longer open. Try again.');
    expect(alert).not.toHaveTextContent('server words');
  });

  it('fails closed on an answer without a findings list', async () => {
    const user = userEvent.setup();
    const { fetcher } = server({
      'POST /api/connections/c1/diagnostics': () =>
        json({ connection_id: 'c1', findings: 'none', counts: { error: 0 } }),
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "The server's answer could not be read, so nothing of it is shown.",
    );
    expect(screen.getByTestId('diagnostics-result')).toBeEmptyDOMElement();
  });

  it('says that the server does not answer', async () => {
    const user = userEvent.setup();
    const { fetcher } = server({
      'POST /api/connections/c1/diagnostics': () => {
        throw new TypeError('network');
      },
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The local server does not answer.');
  });
});

describe('diagnostics view: cancel and stale answers', () => {
  it('Cancel posts the cancel route; the incomplete result says so (STU-DX-008)', async () => {
    const user = userEvent.setup();
    const run = deferred();
    const { fetcher, calls } = server({
      'POST /api/connections/c1/diagnostics': run.answer,
      'POST /api/connections/c1/diagnostics/cancel': () => {
        run.resolve(json(diagnosis([OWNER, INCOMPLETE])));
        return json({ canceled: true }, 202);
      },
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    expect(await screen.findByRole('button', { name: 'Running…' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    expect(screen.getByTestId('diagnostics-message')).toHaveTextContent(
      "The diagnosis is running. It stops at the connection's time limit, or when you cancel it.",
    );
    expect(screen.getByTestId('diagnostics-message')).not.toHaveClass('tb-sr-only');
    expect(screen.getByTestId('diagnostics-result')).toHaveAttribute('aria-busy', 'true');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByTestId('diagnostics-incomplete')).toHaveTextContent(
      'Not every check ran (STU-DX-008), so this result is incomplete.',
    );
    const cancel = writes(calls).at(-1);
    expect(cancel).toEqual(
      expect.objectContaining({
        method: 'POST',
        url: '/api/connections/c1/diagnostics/cancel',
        body: {},
      }),
    );
    expect(cancel?.headers['Content-Type']).toBe('application/json');
    expect(screen.getByTestId('diagnostics-summary')).toHaveTextContent('1 error · 1 note');
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('button', { name: 'Run diagnosis' })).toHaveAttribute(
      'aria-disabled',
      'false',
    );
  });

  it('Cancel while the connection opens drops the request; no diagnosis runs', async () => {
    const user = userEvent.setup();
    const open = deferred();
    const { fetcher, calls } = server({ 'POST /api/connections': open.answer });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    expect(await screen.findByRole('button', { name: 'Opening the connection…' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.getByTestId('diagnostics-message')).toHaveTextContent(
      'Canceled before the diagnosis started.',
    );
    open.resolve(json({ connection_id: 'c1' }, 201));
    await open.promise;
    await settle();
    expect(calls.some((call) => call.url.includes('/diagnostics'))).toBe(false);
    expect(screen.getByTestId('diagnostics-result')).toBeEmptyDOMElement();
    expect(screen.getByRole('button', { name: 'Run diagnosis' })).toHaveAttribute(
      'aria-disabled',
      'false',
    );
  });

  it('a profile switch cancels the running diagnosis and drops its answer', async () => {
    const user = userEvent.setup();
    const run = deferred();
    const { fetcher, calls } = server({ 'POST /api/connections/c1/diagnostics': run.answer });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    await screen.findByRole('button', { name: 'Running…' });
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    expect(screen.getByRole('button', { name: 'Run diagnosis' })).toHaveAttribute(
      'aria-disabled',
      'false',
    );
    await waitFor(() =>
      expect(writes(calls).at(-1)?.url).toBe('/api/connections/c1/diagnostics/cancel'),
    );
    run.resolve(json(diagnosis([OWNER])));
    await run.promise;
    await settle();
    expect(screen.queryByTestId('diagnostics-findings')).toBeNull();
    expect(screen.getByTestId('diagnostics-result')).toBeEmptyDOMElement();
    expect(screen.getByTestId('diagnostics-message')).toBeEmptyDOMElement();
  });

  it('a profile switch drops a connection that opens late', async () => {
    const user = userEvent.setup();
    const open = deferred();
    const { fetcher, calls } = server({ 'POST /api/connections': open.answer });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    await screen.findByRole('button', { name: 'Opening the connection…' });
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    open.resolve(json({ connection_id: 'c1' }, 201));
    await open.promise;
    await settle();
    expect(calls.some((call) => call.url.includes('/diagnostics'))).toBe(false);
    expect(screen.getByTestId('diagnostics-result')).toBeEmptyDOMElement();
  });

  it('leaving the page cancels the running diagnosis', async () => {
    const user = userEvent.setup();
    const run = deferred();
    const { fetcher, calls } = server({ 'POST /api/connections/c1/diagnostics': run.answer });
    const { unmount } = render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    await screen.findByRole('button', { name: 'Running…' });
    unmount();
    await waitFor(() =>
      expect(writes(calls).at(-1)?.url).toBe('/api/connections/c1/diagnostics/cancel'),
    );
    run.resolve(json(diagnosis([OWNER])));
    await run.promise;
  });

  it('leaving the page after a finished run cancels nothing', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server();
    const { unmount } = render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    await screen.findByTestId('diagnostics-findings');
    unmount();
    await settle();
    expect(calls.some((call) => call.url.endsWith('/cancel'))).toBe(false);
  });
});

describe('diagnostics view: caps and fail-closed values', () => {
  it('lists MAX_FINDINGS findings, then "and N more"; exactly MAX_FINDINGS show no such line', async () => {
    const user = userEvent.setup();
    const many = (count: number) =>
      Array.from({ length: count }, (_, index) => finding({ code: `TAB-SQ-${index}` }));
    const { fetcher } = server({
      'POST /api/connections/c1/diagnostics': [
        () => json(diagnosis(many(MAX_FINDINGS + 5))),
        () => json(diagnosis(many(MAX_FINDINGS))),
      ],
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    await screen.findByTestId('diagnostics-findings');
    expect(findings()).toHaveLength(MAX_FINDINGS);
    expect(screen.getByTestId('diagnostics-more')).toHaveTextContent(
      'And 5 more findings, not shown.',
    );
    // The summary counts every finding, the ones not listed included.
    expect(screen.getByTestId('diagnostics-summary')).toHaveTextContent(
      `${MAX_FINDINGS + 5} warnings`,
    );
    await user.click(screen.getByRole('button', { name: 'Run again' }));
    await screen.findByTestId('diagnostics-findings');
    expect(findings()).toHaveLength(MAX_FINDINGS);
    expect(screen.queryByTestId('diagnostics-more')).toBeNull();
  });

  it('cuts long codes, titles and texts with a visible "…"', async () => {
    const user = userEvent.setup();
    const { fetcher } = server({
      'POST /api/connections/c1/diagnostics': () =>
        json(
          diagnosis([
            finding({
              code: 'C'.repeat(MAX_CODE_LENGTH + 1),
              title: 'T'.repeat(MAX_TITLE_LENGTH + 1),
              detail: 'D'.repeat(MAX_TEXT_LENGTH + 1),
              remedy: `${'R'.repeat(MAX_TEXT_LENGTH - 1)}${BIDI}`,
            }),
          ]),
        ),
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    const item = nth(await screen.findAllByTestId('diagnostics-finding'), 0);
    expect(within(item).getByTestId('diagnostics-code').textContent).toBe(
      `${'C'.repeat(MAX_CODE_LENGTH)}…`,
    );
    expect(item).toHaveAccessibleName(`${'T'.repeat(MAX_TITLE_LENGTH)}…`);
    expect(within(item).getByTestId('diagnostics-detail').textContent).toBe(
      `${'D'.repeat(MAX_TEXT_LENGTH)}…`,
    );
    // The escape of the bidi control does not fit: it is cut whole, never split.
    expect(within(item).getByTestId('diagnostics-remedy').textContent).toBe(
      `Remedy: ${'R'.repeat(MAX_TEXT_LENGTH - 1)}…`,
    );
  });

  it('keeps an unknown severity visible with the errors and counts it on its own', async () => {
    const user = userEvent.setup();
    const { fetcher } = server({
      'POST /api/connections/c1/diagnostics': () =>
        json(
          diagnosis(
            [
              NOTE,
              finding({ severity: 'critical', source: 'plugin', title: '' }),
              OWNER,
              'not an object',
              finding({ code: 7 }),
            ],
            { counts: { error: 0, warning: 0, info: 0 } },
          ),
        ),
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    await screen.findByTestId('diagnostics-findings');
    const items = findings();
    expect(items.map((item) => item.dataset.severity)).toEqual(['unknown', 'error', 'info']);
    expect(within(nth(items, 0)).getByTestId('diagnostics-severity')).toHaveTextContent(
      'Unknown severity',
    );
    expect(items[0]).toHaveTextContent('unknown source');
    expect(items[0]).toHaveAccessibleName('(no title)');
    expect(screen.getByTestId('diagnostics-summary')).toHaveTextContent(
      '1 error · 1 note · 1 with an unknown severity',
    );
  });
});

describe('diagnostics view: German', () => {
  it("names Studio's own findings in German and keeps the server's text marked as English", async () => {
    await act(async () => {
      await i18n.changeLanguage('de');
    });
    const user = userEvent.setup();
    const { fetcher } = server({
      'POST /api/connections/c1/diagnostics': () =>
        json(
          diagnosis([
            OWNER,
            HOSTILE,
            INCOMPLETE,
            // A Studio code from another source keeps its own (English) title: only Studio's are translated.
            finding({ code: 'STU-DX-003', source: 'tabrion', title: 'Borrowed code' }),
          ]),
        ),
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.click(screen.getByRole('button', { name: 'Diagnose starten' }));
    await screen.findByTestId('diagnostics-findings');
    const items = findings();
    expect(items[0]).toHaveAccessibleName('Die Rolle besitzt Tabellen');
    expect(within(nth(items, 0)).getByRole('heading')).not.toHaveAttribute('lang');
    expect(within(nth(items, 0)).getByTestId('diagnostics-severity')).toHaveTextContent('Fehler');
    expect(within(nth(items, 0)).getByTestId('diagnostics-remedy')).toHaveTextContent('Abhilfe:');
    // tabrion's title is its own English text.
    expect(within(nth(items, 1)).getByRole('heading')).toHaveAttribute('lang', 'en');
    expect(items[2]).toHaveAccessibleName('Borrowed code');
    expect(within(nth(items, 2)).getByRole('heading')).toHaveAttribute('lang', 'en');
    expect(items[3]).toHaveAccessibleName('Nicht jede Prüfung lief');
    expect(screen.getByTestId('diagnostics-summary')).toHaveTextContent(
      '1 Fehler · 2 Warnungen · 1 Hinweis',
    );
    expect(screen.getByTestId('diagnostics-incomplete')).toHaveTextContent(
      'Nicht jede Prüfung lief (STU-DX-008)',
    );
  });
});

describe('diagnostics API parsing', () => {
  it('fails closed on unexpected values', () => {
    expect(parseDiagnosis(null)).toBeNull();
    expect(parseDiagnosis('text')).toBeNull();
    expect(parseDiagnosis({ connection_id: 'c1' })).toBeNull();
    const parsed = parseDiagnosis({
      connection_id: 5,
      engine: null,
      environment: 'staging',
      ran_at: 'yesterday',
      findings: [
        { code: 'A', severity: 'warning', title: 1, detail: null, remedy: [], source: 'studio' },
        { code: '', severity: 'error' },
        { severity: 'error' },
        null,
        { code: 'B', severity: 'ERROR', source: 'tabrion' },
      ],
      counts: { error: 99 },
    });
    expect(parsed).toEqual({
      connectionId: '',
      engine: '',
      environment: null,
      ranAt: null,
      findings: [
        {
          code: 'B',
          severity: 'unknown',
          title: '',
          detail: '',
          remedy: '',
          source: 'tabrion',
        },
        { code: 'A', severity: 'warning', title: '', detail: '', remedy: '', source: 'studio' },
      ],
    });
    expect(countFindings(parsed?.findings ?? [])).toEqual({
      error: 0,
      warning: 1,
      info: 0,
      unknown: 1,
    });
    expect(parseDiagnosis({ findings: [], ran_at: -1 })?.ranAt).toBeNull();
    expect(parseDiagnosis({ findings: [], ran_at: Number.NaN })?.ranAt).toBeNull();
    expect(parseDiagnosis({ findings: [], ran_at: 2 })?.ranAt).toBe(2000);
  });

  it("keeps the server's order within one severity", () => {
    const parsed = parseDiagnosis({
      findings: [
        finding({ code: 'X-2', severity: 'info' }),
        finding({ code: 'X-1', severity: 'warning' }),
        finding({ code: 'X-3', severity: 'warning' }),
        finding({ code: 'X-0', severity: 'error' }),
      ],
    });
    expect(parsed?.findings.map((item) => item.code)).toEqual(['X-0', 'X-1', 'X-3', 'X-2']);
  });
});

describe('diagnostics view: review fixes of #39 (stu-014-ui-review.md)', () => {
  it('C-1: an out-of-range ran_at shows the findings without a time instead of crashing', async () => {
    const user = userEvent.setup();
    const { fetcher } = server({
      'POST /api/connections/c1/diagnostics': () => json(diagnosis([OWNER], { ran_at: 1e15 })),
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    expect(await screen.findByTestId('diagnostics-findings')).toBeInTheDocument();
    expect(screen.getByTestId('diagnostics-diagnosis').querySelector('time')).toBeNull();
    expect(parseDiagnosis({ findings: [], ran_at: 1e15 })?.ranAt).toBeNull();
    // The largest time a Date holds is 8.64e15 ms; one second more is refused.
    expect(parseDiagnosis({ findings: [], ran_at: 8.64e12 })?.ranAt).toBe(8.64e15);
    expect(parseDiagnosis({ findings: [], ran_at: 8.64e12 + 1 })?.ranAt).toBeNull();
  });

  it('C-2: "Run again" keeps the keyboard focus while it runs and after it ends', async () => {
    const user = userEvent.setup();
    const second = deferred();
    const { fetcher } = server({
      'POST /api/connections/c1/diagnostics': [() => json(diagnosis([OWNER])), second.answer],
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    await screen.findByTestId('diagnostics-findings');
    const again = screen.getByRole('button', { name: 'Run again' });
    again.focus();
    await user.keyboard('{Enter}');
    await screen.findByRole('button', { name: 'Running…' });
    expect(again).toHaveAttribute('aria-disabled', 'true');
    expect(document.activeElement).toBe(again);
    second.resolve(json(diagnosis([])));
    expect(await screen.findByTestId('diagnostics-summary')).toHaveTextContent('No findings');
    expect(document.activeElement).toBe(again);
    expect(again).toHaveAttribute('aria-disabled', 'false');
  });

  it('C-2, L-3: a keyboard cancel keeps the focus on Cancel through "Canceling…" and the end of the run', async () => {
    const user = userEvent.setup();
    const run = deferred();
    const cancel = deferred();
    const { fetcher } = server({
      'POST /api/connections/c1/diagnostics': run.answer,
      'POST /api/connections/c1/diagnostics/cancel': cancel.answer,
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    await screen.findByRole('button', { name: 'Running…' });
    const button = screen.getByRole('button', { name: 'Cancel' });
    button.focus();
    await user.keyboard('{Enter}');
    // L-3: between the click and the answer the page says "Canceling…" and Cancel cannot be pressed twice.
    expect(await screen.findByRole('button', { name: 'Canceling…' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    expect(button).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByTestId('diagnostics-message')).toHaveTextContent('Canceling…');
    await user.keyboard('{Enter}');
    cancel.resolve(json({ canceled: true }, 202));
    run.resolve(json(diagnosis([OWNER, INCOMPLETE])));
    expect(await screen.findByTestId('diagnostics-incomplete')).toBeInTheDocument();
    expect(document.activeElement).toBe(button);
    expect(button).toHaveAttribute('aria-disabled', 'true');
  });

  it('C-2: an aria-disabled button does nothing when pressed', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server();
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run again' }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await settle();
    expect(writes(calls)).toEqual([]);
  });

  it.each([
    ['answers canceled: false', () => json({ canceled: false }, 202)],
    [
      'fails',
      () => {
        throw new TypeError('network');
      },
    ],
  ])('L-1: a cancel that %s says so and can be tried again', async (_name, refused) => {
    const user = userEvent.setup();
    const run = deferred();
    const { fetcher, calls } = server({
      'POST /api/connections/c1/diagnostics': run.answer,
      'POST /api/connections/c1/diagnostics/cancel': [
        refused as Answer,
        () => {
          run.resolve(json(diagnosis([OWNER, INCOMPLETE])));
          return json({ canceled: true }, 202);
        },
      ],
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    await screen.findByRole('button', { name: 'Running…' });
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByTestId('diagnostics-message')).toHaveTextContent(
      'Studio could not cancel the diagnosis.',
    );
    expect(screen.getByRole('button', { name: 'Running…' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveAttribute(
      'aria-disabled',
      'false',
    );
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByTestId('diagnostics-incomplete')).toBeInTheDocument();
    expect(calls.filter((call) => call.url.endsWith('/cancel'))).toHaveLength(2);
  });

  it('L-2: the status region is in the accessibility tree while it is empty', async () => {
    const { fetcher } = server();
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    const region = screen.getByRole('status');
    expect(region).toBe(screen.getByTestId('diagnostics-message'));
    expect(region).toBeEmptyDOMElement();
    expect(region).toHaveClass('tb-sr-only');
  });

  it('L-3: the result head shows the PROD profile name escaped', async () => {
    const user = userEvent.setup();
    const { fetcher } = server({
      'POST /api/connections/c1/diagnostics': () => json(diagnosis([OWNER])),
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    const dialog = screen.getByRole('dialog', { name: 'Production database' });
    await user.click(within(dialog).getByRole('button', { name: 'Connect' }));
    const result = await screen.findByTestId('diagnostics-diagnosis');
    expect(result).toHaveTextContent('measure\\u202E');
    expect(result.textContent).not.toContain(BIDI);
  });

  it('L-3: the summary of exactly one finding counts it', async () => {
    const user = userEvent.setup();
    const { fetcher } = server({
      'POST /api/connections/c1/diagnostics': () => json(diagnosis([OWNER])),
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    expect(await screen.findByTestId('diagnostics-summary')).toHaveTextContent(/^1 error$/);
    expect(findings()).toHaveLength(1);
  });

  it('#40: an answer with a backup field (STU-025) is read; the hints are not shown yet', async () => {
    const user = userEvent.setup();
    const backup = {
      hints: [{ code: 'STU-BK-010', severity: 'warning', title: 'Backup hint text' }],
      facts: { archive_mode: 'off' },
    };
    const { fetcher } = server({
      'POST /api/connections/c1/diagnostics': () => json(diagnosis([OWNER, NOTE], { backup })),
    });
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    expect(await screen.findByTestId('diagnostics-summary')).toHaveTextContent('1 error · 1 note');
    expect(findings()).toHaveLength(2);
    expect(screen.getByTestId('diagnostics-card')).not.toHaveTextContent('Backup hint text');
    expect(screen.getByTestId('diagnostics-card')).not.toHaveTextContent('STU-BK-010');
  });
});

describe('diagnostics view: D-02 on the server (TD-038)', () => {
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
    render(<DiagnosticsView fetcher={fetcher} />);
    await ready();
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
    expect(await screen.findByText(/This profile is marked as PROD now/)).toBeVisible();
    await screen.findByRole('option', { name: 'parts-dev (PROD)' });
    await user.click(screen.getByRole('button', { name: 'Run diagnosis' }));
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
