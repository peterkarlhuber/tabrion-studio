import { apiHeaders } from './session';

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
 * The outcome of one heartbeat: `ok`; `ended` when the server no longer knows this tab's session (401, or 403
 * `access.csrf_invalid`), for example after a restart or when a program on another local port replaced the session
 * cookie (cookie tossing, review S-1); `offline` when the server does not answer.
 */
export type HeartbeatResult = 'ok' | 'ended' | 'offline';

/**
 * Tell the local server that this tab is open. It needs the session (cookie plus the CSRF header); only requests
 * with a valid session and authenticated control requests postpone the idle exit, health checks do not (review
 * F-06, R-5).
 */
export async function sendHeartbeat(
  fetcher: typeof fetch = fetch,
  storage: Pick<Storage, 'getItem'> = sessionStorage,
): Promise<HeartbeatResult> {
  let response: Response;
  try {
    response = await fetcher('/api/heartbeat', {
      cache: 'no-store',
      credentials: 'same-origin',
      headers: apiHeaders(storage),
    });
  } catch {
    return 'offline';
  }
  if (response.ok) {
    return 'ok';
  }
  if (response.status === 401) {
    return 'ended';
  }
  if (response.status === 403) {
    const code = await response
      .json()
      .then((body: { code?: unknown }) => body.code)
      .catch(() => undefined);
    if (code === 'access.csrf_invalid') {
      return 'ended';
    }
  }
  return 'offline';
}

/**
 * Send a heartbeat now and every HEARTBEAT_MS, reporting each result; returns a function that stops it. Once the
 * session has ended the heartbeat stops by itself: it could never succeed again, and the page tells the user to run
 * `tabrion-studio` again (review S-1).
 */
export function startHeartbeat(
  fetcher: typeof fetch = fetch,
  onResult: (result: HeartbeatResult) => void = () => undefined,
): () => void {
  let stopped = false;
  const stop = () => {
    stopped = true;
    window.clearInterval(timer);
  };
  const beat = () => {
    void sendHeartbeat(fetcher).then((result) => {
      if (stopped) return;
      if (result === 'ended') stop();
      onResult(result);
    });
  };
  const timer = window.setInterval(beat, HEARTBEAT_MS);
  beat();
  return stop;
}
