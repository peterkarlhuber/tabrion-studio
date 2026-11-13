import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { App } from './App';
import { fetchHealth, HEARTBEAT_MS, sendHeartbeat, startHeartbeat } from './heartbeat';
import i18n from './i18n';
import { CSRF_HEADER, CSRF_KEY } from './session';

function jsonFetch(body: unknown, ok = true): typeof fetch {
  return vi.fn(async () => new Response(JSON.stringify(body), { status: ok ? 200 : 503 }));
}

/** A server that answers the health check and the heartbeat (204, or the given refusal). */
function server(heartbeat: { status: number; code?: string } = { status: 204 }): typeof fetch {
  return vi.fn(async (input: RequestInfo | URL) => {
    if (String(input) === '/api/heartbeat') {
      return heartbeat.status === 204
        ? new Response(null, { status: 204 })
        : new Response(JSON.stringify({ code: heartbeat.code }), { status: heartbeat.status });
    }
    return new Response(JSON.stringify({ status: 'ok', version: '0.1.0.dev0' }), { status: 200 });
  });
}

afterEach(async () => {
  window.history.replaceState(null, '', '/');
  window.localStorage.clear();
  await act(async () => {
    await i18n.changeLanguage('en');
  });
});

describe('App', () => {
  it('shows the shell with the backend version when the server answers', async () => {
    render(<App fetcher={server()} />);
    expect(screen.getByRole('banner')).toHaveTextContent('Tabrion Studio');
    expect(screen.getByRole('heading', { level: 1, name: 'Welcome' })).toBeInTheDocument();
    expect(await screen.findByText('Local server 0.1.0.dev0')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /No connection/ })).toBeInTheDocument();
    expect(document.title).toBe('Tabrion Studio');
  });

  it.each(['en', 'de'])(
    'STU-050 (%s): the start page leads to every view that exists, each link named once',
    async (lang) => {
      await act(async () => {
        await i18n.changeLanguage(lang);
      });
      render(<App fetcher={server()} />);
      await screen.findByTestId('start-steps');
      expect(
        screen.queryByText(/arrive with the next versions|folgen mit den nächsten/),
      ).toBeNull();
      const steps = within(screen.getByTestId('start-steps')).getAllByRole('link');
      expect(steps.map((link) => link.getAttribute('href'))).toEqual([
        '/connections',
        '/explorer',
        '/sql',
        '/migrations',
        '/diagnostics',
      ]);
      // No step shares a name with the activity bar in either language (review L-2 of stu-050-review.md): a
      // locator by name, which matches substrings and ignores case, finds exactly one link.
      const nav = within(screen.getByRole('navigation', { name: i18n.t('nav.label') }));
      for (const link of steps) {
        const name = link.textContent ?? '';
        expect(nav.queryAllByRole('link', { name: new RegExp(name, 'i') })).toHaveLength(0);
        for (const other of nav.getAllByRole('link')) {
          const label = (other.getAttribute('aria-label') ?? other.textContent ?? '').trim();
          expect(label).not.toBe('');
          expect(name.toLowerCase()).not.toContain(label.toLowerCase());
        }
      }
    },
  );

  it('STU-050: a start-page link navigates in the page, but leaves modifier and middle clicks to the browser', async () => {
    const user = userEvent.setup();
    render(<App fetcher={server()} />);
    await screen.findByText('Local server 0.1.0.dev0');
    expect(screen.getByRole('heading', { level: 2, name: 'Get started' })).toBeInTheDocument();
    const link = screen.getByRole('link', { name: 'Run a query' });
    // Review L-1: like the activity bar, Ctrl/Cmd/Shift/Alt-click and the middle button are not taken over.
    for (const init of [
      { ctrlKey: true },
      { metaKey: true },
      { shiftKey: true },
      { altKey: true },
    ]) {
      expect(fireEvent.click(link, init)).toBe(true); // not prevented
      expect(window.location.pathname).toBe('/');
    }
    expect(fireEvent.click(link, { button: 1 })).toBe(true);
    expect(window.location.pathname).toBe('/');
    await user.click(link);
    expect(window.location.pathname).toBe('/sql');
  });

  it('asks to run tabrion-studio again when the sign-in link was used', async () => {
    render(<App fetcher={server()} session="used" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('already used');
    expect(screen.getByRole('alert').querySelector('code')).toHaveTextContent('tabrion-studio');
    expect(screen.queryByRole('navigation')).toBeNull();
  });

  it('shows no sign-in notice with a session', async () => {
    render(<App fetcher={server()} session="ok" />);
    await screen.findByText('Local server 0.1.0.dev0');
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('says so when the server does not answer', async () => {
    const failing: typeof fetch = vi.fn(async () => {
      throw new TypeError('offline');
    });
    render(<App fetcher={failing} />);
    expect(await screen.findByText('The local server does not answer.')).toBeInTheDocument();
  });

  it('S-1: says the session ended and stops the heartbeat after a 401', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const fetcher = server({ status: 401, code: 'access.session_required' });
      render(<App fetcher={fetcher} />);
      const alert = await screen.findByRole('alert');
      expect(alert).toHaveTextContent(
        'Your session has ended. Run tabrion-studio again to open Studio.',
      );
      expect(screen.getByRole('heading', { name: 'Session ended' })).toBeInTheDocument();
      const beats = () =>
        vi.mocked(fetcher).mock.calls.filter(([url]) => String(url) === '/api/heartbeat').length;
      const before = beats();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(HEARTBEAT_MS * 3);
      });
      expect(beats()).toBe(before);
    } finally {
      vi.useRealTimers();
    }
  });

  it('S-1: a CSRF refusal also ends the session; other refusals do not', async () => {
    expect(await sendHeartbeat(server({ status: 403, code: 'access.csrf_invalid' }))).toBe('ended');
    expect(await sendHeartbeat(server({ status: 403, code: 'access.cross_site' }))).toBe('offline');
    expect(await sendHeartbeat(server({ status: 500 }))).toBe('offline');
  });

  it('navigates between the views and sets the window title', async () => {
    const user = userEvent.setup();
    render(<App fetcher={server()} />);
    const nav = screen.getByRole('navigation', { name: 'Main navigation' });
    await user.click(within(nav).getByRole('link', { name: 'Settings' }));
    expect(screen.getByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/settings');
    expect(within(nav).getByRole('link', { name: 'Settings' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(document.title).toBe('Settings – Tabrion Studio');
    await user.click(within(nav).getByRole('link', { name: 'Connections' }));
    expect(screen.getByRole('heading', { level: 1, name: 'Connections' })).toBeInTheDocument();
    await act(async () => {
      window.history.pushState(null, '', '/nowhere');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(screen.getByRole('heading', { level: 1, name: 'Page not found' })).toBeInTheDocument();
  });

  it('switches the language and the theme in the settings', async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, '', '/settings');
    render(<App fetcher={server()} />);
    await user.click(screen.getByRole('radio', { name: 'Deutsch' }));
    expect(screen.getByRole('heading', { level: 1, name: 'Einstellungen' })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe('de');
    expect(await screen.findByText('Sprache: Deutsch')).toBeInTheDocument();
    await user.click(screen.getByRole('radio', { name: 'Dunkel' }));
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(window.localStorage.getItem('tabrion-studio.theme')).toBe('dark');
    await user.click(screen.getByRole('radio', { name: 'Normal' }));
    expect(document.documentElement.dataset.density).toBe('normal');
    await user.click(screen.getByRole('radio', { name: 'Hell' }));
    await user.click(screen.getByRole('radio', { name: 'Kompakt' }));
    expect(document.documentElement.dataset.theme).toBe('light');
  });
});

describe('fetchHealth', () => {
  it('rejects malformed answers', async () => {
    expect(await fetchHealth(jsonFetch({ status: 1 }))).toBeNull();
    expect(await fetchHealth(jsonFetch({ status: 'ok', version: 'x' }, false))).toBeNull();
  });
});

describe('heartbeat', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('calls /api/heartbeat with the session CSRF header, not the health endpoint', async () => {
    const fetcher = vi.fn(async () => new Response(null, { status: 204 }));
    const storage = { getItem: (key: string) => (key === CSRF_KEY ? 'csrf-value' : null) };
    expect(await sendHeartbeat(fetcher, storage)).toBe('ok');
    expect(fetcher).toHaveBeenCalledWith(
      '/api/heartbeat',
      expect.objectContaining({
        cache: 'no-store',
        credentials: 'same-origin',
        headers: expect.objectContaining({ [CSRF_HEADER]: 'csrf-value' }),
      }),
    );
  });

  it('reports a failed heartbeat', async () => {
    const failing: typeof fetch = vi.fn(async () => {
      throw new TypeError('offline');
    });
    expect(await sendHeartbeat(failing)).toBe('offline');
  });

  it('beats at once and then every interval until stopped', () => {
    vi.useFakeTimers();
    const fetcher = vi.fn(async () => new Response(null, { status: 204 }));
    const stop = startHeartbeat(fetcher);
    expect(fetcher).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(HEARTBEAT_MS * 2);
    expect(fetcher).toHaveBeenCalledTimes(3);
    stop();
    vi.advanceTimersByTime(HEARTBEAT_MS * 2);
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it('stops by itself once the session has ended', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn(async () => new Response('{}', { status: 401 }));
    const results: string[] = [];
    startHeartbeat(fetcher, (result) => results.push(result));
    await vi.advanceTimersByTimeAsync(HEARTBEAT_MS * 3);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(results).toEqual(['ended']);
  });
});
