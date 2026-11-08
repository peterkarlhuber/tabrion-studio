/**
 * STU-006: the page asks for project trust (D-01) and sends back the digest it showed; the server decides.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { parseProject } from '../api/project';
import { readProblem } from '../api/problem';
import { DIFF, REVIEW } from '../harness/trust';
import { CSRF_HEADER, CSRF_KEY } from '../session';
import { ProjectTrust } from './ProjectTrust';

const DIGEST = 'a'.repeat(64);

function project(state: string, extra: Record<string, unknown> = {}) {
  return {
    path: '/work/parts',
    profiles_file: '.tabrion/studio.toml',
    state,
    trusted: state === 'trusted',
    digest: DIGEST,
    review: REVIEW,
    diff: null,
    issues: [],
    ...extra,
  };
}

function server(answers: unknown[], trust: { status: number; code?: string } = { status: 204 }) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const queue = [...answers];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init });
    if (String(input) === '/api/project/trust') {
      return trust.status === 204
        ? new Response(null, { status: 204 })
        : new Response(JSON.stringify({ code: trust.code, kind: 'config', status: trust.status }), {
            status: trust.status,
          });
    }
    const next = queue.length > 1 ? queue.shift() : queue[0];
    return new Response(JSON.stringify(next), { status: 200 });
  });
  return { fetcher: fetcher as unknown as typeof fetch, calls };
}

beforeEach(() => sessionStorage.setItem(CSRF_KEY, 'csrf-value'));
afterEach(() => sessionStorage.clear());

it('asks for trust and sends back the digest it showed, with the CSRF header', async () => {
  const user = userEvent.setup();
  const { fetcher, calls } = server([project('untrusted'), project('trusted')]);
  render(<ProjectTrust fetcher={fetcher} />);
  const dialog = await screen.findByRole('dialog', { name: 'Trust the project profiles?' });
  expect(within(dialog).getByRole('button', { name: "Don't load" })).toHaveFocus();
  await user.click(within(dialog).getByRole('button', { name: 'Trust' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  const post = calls.find((call) => call.url === '/api/project/trust');
  expect(post?.init?.method).toBe('POST');
  expect(JSON.parse(String(post?.init?.body))).toEqual({ digest: DIGEST });
  const headers = post?.init?.headers as Record<string, string>;
  expect(headers[CSRF_HEADER]).toBe('csrf-value');
  expect(headers['Content-Type']).toBe('application/json');
});

it('shows the diff for a changed file', async () => {
  const { fetcher } = server([project('changed', { diff: DIFF })]);
  render(<ProjectTrust fetcher={fetcher} />);
  const dialog = await screen.findByRole('dialog', {
    name: 'The project profiles have changed. Trust them?',
  });
  expect(within(dialog).getByTestId('trust-diff')).toBeInTheDocument();
});

it('shows the new content when the file changed after the dialog opened (studio.trust_stale)', async () => {
  const user = userEvent.setup();
  const newer = project('untrusted', {
    digest: 'b'.repeat(64),
    review: { ...REVIEW, hosts: ['new.host:5432'] },
  });
  const { fetcher } = server([project('untrusted'), newer], {
    status: 409,
    code: 'studio.trust_stale',
  });
  render(<ProjectTrust fetcher={fetcher} />);
  const dialog = await screen.findByRole('dialog');
  await user.click(within(dialog).getByRole('button', { name: 'Trust' }));
  expect(await screen.findByText('new.host:5432')).toBeInTheDocument();
  // Review I-2: the dialog says that it now shows the new content.
  expect(within(screen.getByRole('dialog')).getByTestId('trust-stale')).toHaveTextContent(
    'The file changed after it was shown.',
  );
});

it('says why the project profiles are not loaded when the file is invalid (review I-2)', async () => {
  const user = userEvent.setup();
  const pointer = `profile[0]."evil${String.fromCodePoint(0x202e)}"`;
  const { fetcher } = server([
    project('invalid', {
      review: null,
      issues: [
        { pointer: 'profile[0].path', code: 'config.network_path', severity: 'error' },
        { pointer, code: 'config.type', severity: 'error' },
        { pointer: 'profile[0].x', code: 'config.unknown_key', severity: 'warning' },
      ],
    }),
  ]);
  render(<ProjectTrust fetcher={fetcher} />);
  const notice = await screen.findByTestId('project-invalid');
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(notice).toHaveTextContent('Project profiles not loaded');
  expect(notice).toHaveTextContent('.tabrion/studio.toml cannot be used');
  const items = within(notice).getAllByRole('listitem');
  expect(items).toHaveLength(2); // errors only
  expect(items[0]).toHaveTextContent('profile[0].path config.network_path');
  expect(items[1]?.querySelector('code')?.textContent).toBe('profile[0]."evil\\u202E"');
  await user.click(within(notice).getByRole('button', { name: 'Dismiss' }));
  expect(screen.queryByTestId('project-invalid')).toBeNull();
});

it("Don't load keeps the profiles unused and sends nothing", async () => {
  const user = userEvent.setup();
  const { fetcher, calls } = server([project('untrusted')]);
  render(<ProjectTrust fetcher={fetcher} />);
  await user.click(await screen.findByRole('button', { name: "Don't load" }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(calls.some((call) => call.url === '/api/project/trust')).toBe(false);
});

it.each([
  ['trusted', project('trusted')],
  ['none', project('none', { review: null, digest: null })],
  ['invalid', project('invalid', { review: null })],
  ['not a project answer', { status: 'ok', version: '0.1.0.dev0' }],
  ['garbage', 'x'],
])('shows no dialog for %s', async (_name, answer) => {
  const { fetcher, calls } = server([answer]);
  render(<ProjectTrust fetcher={fetcher} />);
  await waitFor(() => expect(calls.length).toBeGreaterThan(0));
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('parses defensively', () => {
  expect(parseProject(null)).toBeNull();
  expect(parseProject({ state: 'pwned' })).toBeNull();
  const parsed = parseProject({
    state: 'untrusted',
    digest: 5,
    review: { connections: [{ name: 1 }, { name: 'ok' }], hosts: ['h', 3], files: [{ path: 'p' }] },
  });
  expect(parsed?.digest).toBeNull();
  expect(parsed?.review?.connections.map((c) => c.name)).toEqual(['ok']);
  expect(parsed?.review?.hosts).toEqual(['h']);
  expect(parsed?.review?.files).toEqual([{ path: 'p', kind: 'database', outside: true }]);
});

it('reads problems', async () => {
  const problem = await readProblem(
    new Response(
      JSON.stringify({ code: 'studio.internal_error', kind: 'internal', log_id: '0123456789ab' }),
      {
        status: 500,
      },
    ),
  );
  expect(problem).toEqual({
    code: 'studio.internal_error',
    kind: 'internal',
    status: 500,
    logId: '0123456789ab',
  });
  expect((await readProblem(new Response('x', { status: 502 }))).code).toBe('studio.unknown');
  expect((await readProblem(new Response('{"log_id":"<b>"}', { status: 500 }))).logId).toBeNull();
});
