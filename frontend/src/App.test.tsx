import { render, screen } from '@testing-library/react';

import { App } from './App';
import { fetchHealth, HEARTBEAT_MS, sendHeartbeat, startHeartbeat } from './heartbeat';
import { CSRF_HEADER, CSRF_KEY } from './session';

function jsonFetch(body: unknown, ok = true): typeof fetch {
  return vi.fn(async () => new Response(JSON.stringify(body), { status: ok ? 200 : 503 }));
}

describe('App', () => {
  it('shows the backend version when the server answers', async () => {
    render(<App fetcher={jsonFetch({ status: 'ok', version: '0.1.0.dev0' })} />);
    expect(screen.getByRole('heading', { name: 'Tabrion Studio' })).toBeInTheDocument();
    expect(await screen.findByText('Local server 0.1.0.dev0: ok')).toBeInTheDocument();
  });

  it('asks to run tabrion-studio again when the sign-in link was used', async () => {
    render(<App fetcher={jsonFetch({ status: 'ok', version: '0.1.0.dev0' })} session="used" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('already used');
  });

  it('shows no sign-in notice with a session', async () => {
    render(<App fetcher={jsonFetch({ status: 'ok', version: '0.1.0.dev0' })} session="ok" />);
    await screen.findByText('Local server 0.1.0.dev0: ok');
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('says so when the server does not answer', async () => {
    const failing: typeof fetch = vi.fn(async () => {
      throw new TypeError('offline');
    });
    render(<App fetcher={failing} />);
    expect(await screen.findByText('The local server does not answer.')).toBeInTheDocument();
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
    expect(await sendHeartbeat(fetcher, storage)).toBe(true);
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
    expect(await sendHeartbeat(failing)).toBe(false);
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
});
