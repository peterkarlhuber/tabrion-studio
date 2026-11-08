import { apiHeaders, CSRF_HEADER, CSRF_KEY, type SessionEnv, startSession } from './session';

const TOKEN = 'a'.repeat(43);

function memoryStorage(initial: Record<string, string> = {}) {
  const items = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => {
      items.set(key, value);
    },
    items,
  };
}

function env(
  pathname: string,
  hash: string,
  answer: () => Promise<Response>,
  storage = memoryStorage(),
): SessionEnv & { replaced: string[]; fetcher: ReturnType<typeof vi.fn> } {
  const replaced: string[] = [];
  return {
    fetcher: vi.fn(answer),
    location: { pathname, hash },
    history: {
      replaceState: (_data: unknown, _unused: string, url?: string | URL | null) => {
        replaced.push(String(url));
      },
    },
    storage,
    replaced,
  };
}

const json =
  (body: unknown, status = 200) =>
  async () =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('startSession', () => {
  it('redeems the launch token from the fragment and keeps the CSRF value in sessionStorage', async () => {
    const storage = memoryStorage();
    const e = env('/launch', `#${TOKEN}`, json({ csrf: 'csrf-value' }), storage);
    expect(await startSession(e)).toBe('ok');
    expect(e.replaced).toEqual(['/']); // the token leaves the address bar and the history
    expect(storage.items.get(CSRF_KEY)).toBe('csrf-value');
    const [url, init] = e.fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/session');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('same-origin');
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json');
    expect(JSON.parse(init.body as string)).toEqual({ token: TOKEN });
  });

  it('removes the token from the URL before the request is sent', async () => {
    let replacedFirst = false;
    const e = env('/launch', `#${TOKEN}`, async () => {
      replacedFirst = e.replaced.length === 1;
      return new Response(JSON.stringify({ csrf: 'x' }), { status: 200 });
    });
    await startSession(e);
    expect(replacedFirst).toBe(true);
  });

  it.each([
    ['access.launch_used', 'used'],
    ['access.launch_expired', 'expired'],
    ['access.launch_invalid', 'invalid'],
    ['something.else', 'invalid'],
  ])('reports a refused token (%s)', async (code, state) => {
    const storage = memoryStorage();
    expect(await startSession(env('/launch', `#${TOKEN}`, json({ code }, 401), storage))).toBe(
      state,
    );
    expect(storage.items.has(CSRF_KEY)).toBe(false);
  });

  it('reports an unreachable server', async () => {
    const failing = async (): Promise<Response> => {
      throw new TypeError('offline');
    };
    expect(await startSession(env('/launch', `#${TOKEN}`, failing))).toBe('offline');
  });

  it('keeps an existing session of this tab without a launch token', async () => {
    const e = env('/', '', json({}), memoryStorage({ [CSRF_KEY]: 'kept' }));
    expect(await startSession(e)).toBe('ok');
    expect(e.fetcher).not.toHaveBeenCalled();
    expect(await startSession(env('/', '', json({})))).toBe('none');
    expect(await startSession(env('/launch', '', json({})))).toBe('none');
  });
});

describe('startSession in the browser', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it('calls fetch on window, not as a method of another object ("Illegal invocation")', async () => {
    const receivers: unknown[] = [];
    vi.stubGlobal('fetch', function (this: unknown) {
      receivers.push(this);
      return Promise.resolve(new Response(JSON.stringify({ csrf: 'c' }), { status: 200 }));
    });
    window.history.replaceState(null, '', `/launch#${TOKEN}`);
    expect(await startSession()).toBe('ok');
    expect([undefined, window, globalThis]).toContain(receivers[0]);
    expect(window.location.pathname).toBe('/');
    expect(window.location.hash).toBe('');
    expect(sessionStorage.getItem(CSRF_KEY)).toBe('c');
  });
});

describe('apiHeaders', () => {
  it('adds the CSRF header only when the tab has a session', () => {
    expect(apiHeaders(memoryStorage({ [CSRF_KEY]: 'v' }))).toEqual({
      Accept: 'application/json',
      [CSRF_HEADER]: 'v',
    });
    expect(apiHeaders(memoryStorage())).toEqual({ Accept: 'application/json' });
  });
});
