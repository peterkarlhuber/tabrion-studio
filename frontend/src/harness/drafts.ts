/**
 * A fake of the drafts routes (TD-033, review F-5) for the e2e harness and the jsdom tests: `GET /api/drafts` and
 * `PUT /api/drafts` with the server's merge by tab (`known`) and its refusal of any tab field it does not know (a
 * parameter value has no place in a draft).
 *
 * The harness page has no server that outlives a page load, so its fake keeps the document in the harness's own
 * `sessionStorage` key (`HARNESS_DRAFTS_KEY`, development server only; the real Studio stores drafts in its state
 * database and nothing in browser storage, `e2e/drafts-backend.spec.ts`). The jsdom tests keep it in memory.
 */

export const HARNESS_DRAFTS_KEY = 'tabrion-harness.fake-server-drafts';

const FIELDS = new Set([
  'id',
  'title',
  'profile_id',
  'sql',
  'saved_sql',
  'script_path',
  'script_digest',
  'script_crlf',
]);

interface Doc {
  tabs: Record<string, unknown>[];
  active_tab: string;
}

export interface DraftsLog {
  /** Every document the page sent with `PUT /api/drafts`. */
  puts: Record<string, unknown>[];
  gets: number;
}

export interface DraftsServerOptions {
  /** The stored document to start with (the server's answer shape). */
  seed?: Doc | null;
  /** Where the fake keeps its document between page loads (the harness); in memory when absent. */
  storage?: Pick<Storage, 'getItem' | 'setItem'>;
  /** Answer every request with this problem (`studio.state_unavailable`: the state database cannot be used). */
  unavailable?: boolean;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export function createDraftsServer(options: DraftsServerOptions = {}) {
  let memory: Doc | null = options.seed ?? null;
  const load = (): Doc | null => {
    if (options.storage === undefined) return memory;
    try {
      return JSON.parse(options.storage.getItem(HARNESS_DRAFTS_KEY) ?? 'null') as Doc | null;
    } catch {
      return null;
    }
  };
  const store = (doc: Doc | null) => {
    memory = doc;
    options.storage?.setItem(HARNESS_DRAFTS_KEY, JSON.stringify(doc));
  };
  if (options.seed !== undefined) store(options.seed);
  const log: DraftsLog = { puts: [], gets: 0 };

  const handle = (url: string, init?: RequestInit): Response | null => {
    if (url !== '/api/drafts') return null;
    if (options.unavailable === true) {
      return json({ code: 'studio.state_unavailable', status: 503 }, 503);
    }
    const method = init?.method ?? 'GET';
    if (method === 'GET') {
      log.gets += 1;
      return json({ drafts: load() });
    }
    const body = JSON.parse(String(init?.body)) as {
      tabs: Record<string, unknown>[];
      active_tab: string;
      known?: string[];
    };
    log.puts.push(body);
    if (body.tabs.some((tab) => Object.keys(tab).some((key) => !FIELDS.has(key)))) {
      return json({ code: 'studio.invalid_request', status: 422 }, 422);
    }
    const seen = new Set([...body.tabs.map((tab) => tab.id), ...(body.known ?? [])]);
    const others = body.known === undefined ? [] : (load()?.tabs ?? []);
    const tabs = [...body.tabs, ...others.filter((tab) => !seen.has(tab.id))];
    store(tabs.length === 0 ? null : { tabs, active_tab: body.active_tab });
    return json({ tabs: tabs.length });
  };

  return { handle, log, current: load };
}
