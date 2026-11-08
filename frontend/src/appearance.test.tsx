/**
 * TD-021: language, theme and density are saved to Studio's settings.toml (the browser forgets them, because the
 * port and so the origin change at every start) and applied from it when the page opens.
 */
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {
  chooseTheme,
  parseAppearance,
  rememberAppearance,
  startAppearanceSync,
  useAppearanceSync,
} from './appearance';
import i18n from './i18n';
import { CSRF_KEY } from './session';
import { setDensity, setThemePreference } from './theme';
import { SettingsView } from './views/Views';

const DIGEST = 'd'.repeat(64);
type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

function settingsBody(
  settings: Record<string, unknown> = {},
  file: Record<string, unknown> = { digest: DIGEST, hand_written: false },
  issues: unknown[] = [],
) {
  return {
    settings: {
      language: 'system',
      theme: 'system',
      density: 'compact',
      log_sql: false,
      limits: { max_rows: 10000, statement_timeout_ms: 30000 },
      ...settings,
    },
    file,
    issues,
  };
}

/** A server whose GET answers come from `gets` in turn (the last one repeats) and whose PUTs from `puts`. */
function server(gets: unknown[], puts: (() => Response)[] = []) {
  const calls: Call[] = [];
  let get = 0;
  let put = 0;
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = {
      url: String(input),
      method: init?.method ?? 'GET',
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
    };
    calls.push(call);
    if (call.method === 'GET') {
      const body = gets[Math.min(get, gets.length - 1)];
      get += 1;
      return new Response(JSON.stringify(body), { status: 200 });
    }
    const answer = puts[Math.min(put, puts.length - 1)];
    put += 1;
    return answer
      ? answer()
      : new Response(JSON.stringify({ digest: 'e'.repeat(64), settings: {} }), { status: 200 });
  });
  return { fetcher: fetcher as unknown as typeof fetch, calls };
}

const stale = () =>
  new Response(JSON.stringify({ code: 'studio.config_stale', kind: 'config', status: 409 }), {
    status: 409,
  });

const puts = (calls: Call[]) => calls.filter((call) => call.method === 'PUT');

beforeEach(() => {
  sessionStorage.setItem(CSRF_KEY, 'csrf-value');
});

afterEach(async () => {
  sessionStorage.clear();
  window.localStorage.clear();
  setThemePreference('system');
  setDensity('compact');
  await act(async () => {
    await i18n.changeLanguage('en');
  });
});

describe('parseAppearance', () => {
  it('takes known values and falls back to the defaults', () => {
    expect(
      parseAppearance(settingsBody({ language: 'de', theme: 'dark', density: 'normal' })),
    ).toEqual({
      language: 'de',
      theme: 'dark',
      density: 'normal',
    });
    expect(parseAppearance({ settings: { language: 'fr', theme: 'sepia', density: 9 } })).toEqual({
      language: 'system',
      theme: 'system',
      density: 'compact',
    });
    expect(parseAppearance({ status: 'ok', version: '0.1.0' })).toBeNull();
    expect(parseAppearance(null)).toBeNull();
  });
});

describe('at the start', () => {
  it('applies the choices of the file', async () => {
    const { fetcher } = server([
      settingsBody({ language: 'de', theme: 'dark', density: 'normal' }),
    ]);
    startAppearanceSync(fetcher);
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe('dark'));
    expect(document.documentElement.lang).toBe('de');
    expect(document.documentElement.dataset.density).toBe('normal');
  });

  it('leaves the browser preference alone where the file has the default', async () => {
    setThemePreference('dark');
    const { fetcher, calls } = server([settingsBody()]);
    startAppearanceSync(fetcher);
    await waitFor(() => expect(calls).toHaveLength(1));
    await act(async () => undefined);
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(document.documentElement.lang).toBe('en');
  });

  it('ignores an answer that is not the settings, and an answer after the stop', async () => {
    const { fetcher } = server([{ status: 'ok' }]);
    startAppearanceSync(fetcher);
    const late = server([settingsBody({ theme: 'dark' })]);
    const stop = startAppearanceSync(late.fetcher);
    stop();
    await act(async () => undefined);
    expect(document.documentElement.dataset.theme).toBe('light');
  });
});

describe('a choice', () => {
  it('is saved alone, with the fresh digest, the CSRF value and JSON', async () => {
    const { fetcher, calls } = server([settingsBody()]);
    startAppearanceSync(fetcher);
    expect(await rememberAppearance({ theme: 'dark' })).toEqual({ kind: 'saved' });
    const [put] = puts(calls);
    expect(put?.url).toBe('/api/settings');
    expect(put?.body).toEqual({ settings: { theme: 'dark' }, digest: DIGEST });
    expect(put?.headers['Content-Type']).toBe('application/json');
    expect(put?.headers['X-Tabrion-CSRF']).toBe('csrf-value');
  });

  it('reads the file again once when it changed in between', async () => {
    const second = 'f'.repeat(64);
    const { fetcher, calls } = server(
      [settingsBody(), settingsBody(), settingsBody({}, { digest: second, hand_written: false })],
      [stale, () => new Response(JSON.stringify({ digest: DIGEST }), { status: 200 })],
    );
    startAppearanceSync(fetcher);
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(await rememberAppearance({ density: 'normal' })).toEqual({ kind: 'saved' });
    expect(puts(calls).map((call) => (call.body as { digest: string }).digest)).toEqual([
      DIGEST,
      second,
    ]);
  });

  it('gives up after a second stale answer', async () => {
    const { fetcher, calls } = server([settingsBody()], [stale]);
    startAppearanceSync(fetcher);
    expect(await rememberAppearance({ theme: 'dark' })).toEqual({
      kind: 'local',
      reason: 'unavailable',
    });
    expect(puts(calls)).toHaveLength(2);
  });

  it('never changes a file with issues or a hand-written one', async () => {
    const issues = server([
      settingsBody({}, undefined, [
        { pointer: 'settings.x', code: 'config.type', severity: 'error' },
      ]),
    ]);
    startAppearanceSync(issues.fetcher);
    expect(await rememberAppearance({ theme: 'dark' })).toEqual({
      kind: 'local',
      reason: 'issues',
    });
    expect(puts(issues.calls)).toEqual([]);
    const hand = server([settingsBody({}, { digest: DIGEST, hand_written: true })]);
    startAppearanceSync(hand.fetcher);
    expect(await rememberAppearance({ theme: 'dark' })).toEqual({
      kind: 'local',
      reason: 'hand_written',
    });
    expect(puts(hand.calls)).toEqual([]);
    // A warning (an unknown key) does not block it.
    const warning = server([
      settingsBody({}, undefined, [
        { pointer: 'settings.y', code: 'config.unknown_key', severity: 'warning' },
      ]),
    ]);
    startAppearanceSync(warning.fetcher);
    expect(await rememberAppearance({ theme: 'dark' })).toEqual({ kind: 'saved' });
  });

  it('stays in the browser without a server or when the server fails', async () => {
    expect(await rememberAppearance({ theme: 'dark' })).toEqual({
      kind: 'local',
      reason: 'unavailable',
    });
    const failing = vi.fn(async () => {
      throw new TypeError('offline');
    }) as unknown as typeof fetch;
    startAppearanceSync(failing);
    expect(await rememberAppearance({ theme: 'dark' })).toEqual({
      kind: 'local',
      reason: 'unavailable',
    });
  });

  it('saves one after the other, in the order chosen', async () => {
    const { fetcher, calls } = server([settingsBody()]);
    startAppearanceSync(fetcher);
    chooseTheme('dark');
    chooseTheme('light');
    await rememberAppearance({ density: 'normal' });
    expect(puts(calls).map((call) => call.body)).toEqual([
      { settings: { theme: 'dark' }, digest: DIGEST },
      { settings: { theme: 'light' }, digest: DIGEST },
      { settings: { density: 'normal' }, digest: DIGEST },
    ]);
    expect(document.documentElement.dataset.theme).toBe('light');
  });
});

function SyncProbe() {
  return <span data-testid="probe">{useAppearanceSync().kind}</span>;
}

describe('the settings view', () => {
  it('saves a choice at once and says where it went', async () => {
    const { fetcher, calls } = server([settingsBody()]);
    startAppearanceSync(fetcher);
    render(
      <>
        <SettingsView fetcher={fetcher} />
        <SyncProbe />
      </>,
    );
    await screen.findByLabelText('Maximum rows per result');
    await userEvent.click(screen.getByRole('radio', { name: 'Dark' }));
    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('saved'));
    expect(puts(calls).at(-1)?.body).toEqual({ settings: { theme: 'dark' }, digest: DIGEST });
    expect(screen.getByTestId('appearance-sync')).toHaveTextContent(
      'Saved in settings.toml as soon as you choose.',
    );
    await userEvent.click(screen.getByRole('radio', { name: 'Deutsch' }));
    await waitFor(() =>
      expect(puts(calls).at(-1)?.body).toEqual({ settings: { language: 'de' }, digest: DIGEST }),
    );
  });

  it('says so when the choice stays in this browser', async () => {
    const { fetcher } = server([settingsBody({}, { digest: DIGEST, hand_written: true })]);
    startAppearanceSync(fetcher);
    render(<SettingsView fetcher={fetcher} />);
    await screen.findByLabelText('Maximum rows per result');
    await userEvent.click(screen.getByRole('radio', { name: 'Normal' }));
    expect(
      await screen.findByText(/Kept in this browser only: your settings.toml was written by hand/),
    ).toBeVisible();
    expect(document.documentElement.dataset.density).toBe('normal'); // applied all the same
  });

  it('the settings form keeps what was typed and saves with the new digest after a choice', async () => {
    const after = 'a'.repeat(64);
    const { fetcher, calls } = server([
      settingsBody(),
      settingsBody(),
      settingsBody({ theme: 'dark' }, { digest: after, hand_written: false }),
    ]);
    startAppearanceSync(fetcher);
    render(<SettingsView fetcher={fetcher} />);
    const rows = await screen.findByLabelText('Maximum rows per result');
    await userEvent.clear(rows);
    await userEvent.type(rows, '500');
    await userEvent.click(screen.getByRole('radio', { name: 'Dark' }));
    await waitFor(() => expect(puts(calls)).toHaveLength(1));
    await waitFor(() =>
      expect(calls.filter((c) => c.method === 'GET').length).toBeGreaterThanOrEqual(3),
    );
    await act(async () => undefined);
    expect(rows).toHaveValue('500');
    await userEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(puts(calls)).toHaveLength(2));
    expect((puts(calls)[1]?.body as { digest: string }).digest).toBe(after);
  });
});
