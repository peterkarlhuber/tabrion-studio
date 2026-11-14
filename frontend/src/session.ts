/**
 * Sign-in through the one-time launch link (STU-003, 03 section 4.1, review F-02/F-09).
 *
 * `tabrion-studio` opens `http://127.a.b.c:<port>/launch#<token>` (a fresh random loopback address for every start,
 * finding A-01; `127.0.0.1` where only that exists). The token is in the fragment, so it never
 * reaches a server log or a Referer. This page removes it from the address bar and the history at once, sends it
 * to `POST /api/session` and gets the session cookie (HttpOnly) plus a CSRF value. The CSRF value lives only in
 * `sessionStorage`, which is scoped to this origin including the port: a program on another local port that
 * receives the cookie (cookies are not port-scoped) never sees it. Every API request sends it as `X-Tabrion-CSRF`.
 */

export const CSRF_HEADER = 'X-Tabrion-CSRF';
export const CSRF_KEY = 'tabrion-studio.csrf';
export const LAUNCH_PATH = '/launch';

export type SessionState = 'ok' | 'used' | 'expired' | 'invalid' | 'none' | 'offline';

export interface SessionEnv {
  fetcher: typeof fetch;
  location: Pick<Location, 'pathname' | 'hash'>;
  history: Pick<History, 'replaceState'>;
  storage: Pick<Storage, 'getItem' | 'setItem'>;
}

function browserEnv(): SessionEnv {
  return {
    // Called as a method of this object, `fetch` itself would throw "Illegal invocation".
    fetcher: (input, init) => window.fetch(input, init),
    location: window.location,
    history: window.history,
    storage: sessionStorage,
  };
}

/** The CSRF value of this tab's session, if it has one. */
export function readCsrf(storage: Pick<Storage, 'getItem'> = sessionStorage): string | null {
  try {
    return storage.getItem(CSRF_KEY);
  } catch {
    return null;
  }
}

/** Headers for a request to the Studio API: JSON, plus the CSRF value of this tab's session. */
export function apiHeaders(
  storage: Pick<Storage, 'getItem'> = sessionStorage,
): Record<string, string> {
  const csrf = readCsrf(storage);
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (csrf !== null) {
    headers[CSRF_HEADER] = csrf;
  }
  return headers;
}

const REFUSALS: Record<string, SessionState> = {
  'access.launch_used': 'used',
  'access.launch_expired': 'expired',
};

/** Redeem the launch token of the current URL, if there is one; otherwise report whether this tab has a session. */
export async function startSession(env: SessionEnv = browserEnv()): Promise<SessionState> {
  if (env.location.pathname !== LAUNCH_PATH) {
    return readCsrf(env.storage) === null ? 'none' : 'ok';
  }
  const token = env.location.hash.replace(/^#/, '');
  // The token leaves the address bar and the session history before anything else happens.
  env.history.replaceState(null, '', '/');
  if (token === '') {
    return readCsrf(env.storage) === null ? 'none' : 'ok';
  }
  try {
    const response = await env.fetcher('/api/session', {
      method: 'POST',
      cache: 'no-store',
      credentials: 'same-origin',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    const body = (await response.json()) as { csrf?: unknown; code?: unknown };
    if (response.ok && typeof body.csrf === 'string') {
      env.storage.setItem(CSRF_KEY, body.csrf);
      return 'ok';
    }
    return (typeof body.code === 'string' && REFUSALS[body.code]) || 'invalid';
  } catch {
    return 'offline';
  }
}
