/** Interval of the heartbeat that keeps the local server from its idle exit (src/tabrion_studio/idle.py). */
export const HEARTBEAT_MS = 60_000;

export interface Health {
  status: string;
  version: string;
}

/** Ask the local server for its health document; null if it does not answer. */
export async function fetchHealth(fetcher: typeof fetch = fetch): Promise<Health | null> {
  try {
    const response = await fetcher('/api/health', {
      cache: 'no-store',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) {
      return null;
    }
    const body = (await response.json()) as Partial<Health>;
    if (typeof body.status !== 'string' || typeof body.version !== 'string') {
      return null;
    }
    return { status: body.status, version: body.version };
  } catch {
    return null;
  }
}

/**
 * Tell the local server that this tab is open. Only this request (sent by the browser with
 * `Sec-Fetch-Site: same-origin`) and authenticated control requests postpone the idle exit;
 * health checks do not (review F-06).
 */
export async function sendHeartbeat(fetcher: typeof fetch = fetch): Promise<boolean> {
  try {
    const response = await fetcher('/api/heartbeat', {
      cache: 'no-store',
      credentials: 'same-origin',
    });
    return response.ok;
  } catch {
    return false;
  }
}

/** Send a heartbeat now and every HEARTBEAT_MS; returns a function that stops it. */
export function startHeartbeat(fetcher: typeof fetch = fetch): () => void {
  void sendHeartbeat(fetcher);
  const timer = window.setInterval(() => {
    void sendHeartbeat(fetcher);
  }, HEARTBEAT_MS);
  return () => window.clearInterval(timer);
}
