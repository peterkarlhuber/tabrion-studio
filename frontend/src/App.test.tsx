import { render, screen } from '@testing-library/react';

import { App } from './App';
import { fetchHealth, HEARTBEAT_MS, sendHeartbeat, startHeartbeat } from './heartbeat';

function jsonFetch(body: unknown, ok = true): typeof fetch {
  return vi.fn(async () => new Response(JSON.stringify(body), { status: ok ? 200 : 503 }));
}

describe('App', () => {
  it('shows the backend version when the server answers', async () => {
    render(<App fetcher={jsonFetch({ status: 'ok', version: '0.1.0.dev0' })} />);
    expect(screen.getByRole('heading', { name: 'Tabrion Studio' })).toBeInTheDocument();
    expect(await screen.findByText('Local server 0.1.0.dev0: ok')).toBeInTheDocument();
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

  it('calls /api/heartbeat, not the health endpoint', async () => {
    const fetcher = vi.fn(async () => new Response(null, { status: 204 }));
    expect(await sendHeartbeat(fetcher)).toBe(true);
    expect(fetcher).toHaveBeenCalledWith(
      '/api/heartbeat',
      expect.objectContaining({ cache: 'no-store' }),
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
