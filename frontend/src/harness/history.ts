/**
 * A fake of STU-020's routes for the e2e harness and the jsdom tests: the query history, favorite and recently
 * opened profiles, the project's `.sql` scripts and, for the SQL editor's completion, a catalog with one table's
 * columns. Like the server, the history holds statements and parameter names, never a value.
 */

export interface HistoryServerOptions {
  /** The history is off (`[history] enabled = false`). */
  disabled?: boolean;
  /** Favorite profile ids. */
  favorites?: string[];
  /** Recently opened profile ids, the newest first. */
  recent?: string[];
  /** The scripts of the project by path. */
  scripts?: Record<string, string>;
}

export interface HistoryLog {
  deletes: Record<string, unknown>[];
  favoritePuts: Record<string, unknown>[];
  scriptPuts: Record<string, unknown>[];
  catalogs: string[];
  tables: string[];
}

const NOW = Date.UTC(2026, 10, 13, 9, 30) / 1000;

export const HISTORY_ENTRIES = [
  {
    id: 3,
    profile_id: 'project:parts-dev',
    environment: 'dev',
    engine: 'sqlite',
    sql: 'select * from parts where id = :id',
    sql_truncated: false,
    param_names: ['id'],
    state: 'done',
    row_count: 1,
    elapsed_ms: 2.5,
    code: null,
    finished_at: NOW,
  },
  {
    id: 2,
    profile_id: 'user:measure-central',
    environment: 'prod',
    engine: 'postgresql',
    sql: 'select count(*)\nfrom measurement',
    sql_truncated: false,
    param_names: [],
    state: 'failed',
    row_count: null,
    elapsed_ms: 12,
    code: 'db.permission',
    finished_at: NOW - 3600,
  },
  {
    id: 1,
    profile_id: 'project:parts-dev',
    environment: 'dev',
    engine: 'sqlite',
    sql: 'select 1',
    sql_truncated: false,
    param_names: [],
    state: 'done',
    row_count: 1,
    elapsed_ms: 0.4,
    code: null,
    finished_at: NOW - 7200,
  },
];

/** A hex digest of a text (not SHA-256: the fake only needs one that changes with the text). */
function digestOf(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) h = Math.imul(h ^ text.charCodeAt(i), 16777619) >>> 0;
  return h.toString(16).padStart(8, '0').repeat(8);
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export function createHistoryServer(options: HistoryServerOptions = {}) {
  let entries = HISTORY_ENTRIES.map((entry) => ({ ...entry }));
  let favorites = [...(options.favorites ?? [])];
  const recent = [...(options.recent ?? [])];
  const scripts = new Map(Object.entries(options.scripts ?? {}));
  const log: HistoryLog = {
    deletes: [],
    favoritePuts: [],
    scriptPuts: [],
    catalogs: [],
    tables: [],
  };

  const favoritesState = () => ({
    favorites: favorites.map((id, index) => ({ profile_id: id, added_at: NOW - index })),
    recent: recent.map((id, index) => ({ profile_id: id, opened_at: NOW - index })),
  });

  const handle = (url: string, init?: RequestInit): Response | null => {
    const method = init?.method ?? 'GET';
    const body =
      typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : {};
    if (url.startsWith('/api/history')) {
      if (method === 'DELETE') {
        log.deletes.push(body);
        const before = entries.length;
        entries = entries.filter((entry) =>
          body.id !== undefined
            ? entry.id !== body.id
            : body.profile_id !== undefined
              ? entry.profile_id !== body.profile_id
              : false,
        );
        return json({ deleted: before - entries.length });
      }
      const query = new URLSearchParams(url.split('?')[1] ?? '');
      const profile = query.get('profile_id');
      const q = query.get('q');
      const shown = entries.filter(
        (entry) =>
          (profile === null || entry.profile_id === profile) &&
          (q === null || entry.sql.toLowerCase().includes(q.toLowerCase())),
      );
      return json({
        enabled: options.disabled !== true,
        retention_days: 30,
        entries: shown,
        next_before: null,
      });
    }
    if (url === '/api/favorites') {
      if (method === 'PUT') {
        log.favoritePuts.push(body);
        const id = String(body.profile_id);
        favorites = favorites.filter((item) => item !== id);
        if (body.favorite === true) favorites.push(id);
      }
      return json(favoritesState());
    }
    if (url === '/api/scripts') {
      return json({
        scripts: [...scripts.entries()]
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([path, content]) => ({ path, size: content.length, modified_at: NOW })),
        truncated: false,
      });
    }
    if (url.startsWith('/api/scripts/file')) {
      if (method === 'PUT') {
        log.scriptPuts.push(body);
        const path = String(body.path);
        const current = scripts.get(path);
        if (current !== undefined && body.overwrite !== true) {
          if (body.digest === null) return json({ code: 'studio.script_exists', status: 409 }, 409);
          if (body.digest !== digestOf(current)) {
            return json({ code: 'studio.script_stale', status: 409 }, 409);
          }
        }
        // The fake project has no folder `missing/` (Studio creates no folders).
        if (path.startsWith('missing/')) {
          return json({ code: 'studio.script_folder_missing', status: 404 }, 404);
        }
        scripts.set(path, String(body.content));
        return json({ path, digest: digestOf(String(body.content)) });
      }
      const path = new URLSearchParams(url.split('?')[1] ?? '').get('path') ?? '';
      const content = scripts.get(path);
      return content === undefined
        ? json({ code: 'studio.script_not_found', status: 404 }, 404)
        : json({ path, content, digest: digestOf(content) });
    }
    const catalog = /^\/api\/connections\/([^/?]+)\/catalog(\?.*)?$/.exec(url);
    if (catalog !== null) {
      log.catalogs.push(url);
      const id = decodeURIComponent(catalog[1] ?? '');
      // A PROD connection has no catalog read yet: completion asks for it only after the user's click.
      if ((catalog[2] ?? '').includes('cached_only=true')) {
        return json({ code: 'studio.catalog_not_loaded', status: 404 }, 404);
      }
      const sqlite = id.includes('parts-dev');
      return json({
        connection_id: id,
        engine: sqlite ? 'sqlite' : 'postgresql',
        schemas: [sqlite ? 'main' : 'public'],
        objects: [
          { schema: sqlite ? 'main' : 'public', name: 'parts', kind: 'table' },
          { schema: sqlite ? 'main' : 'public', name: 'suppliers', kind: 'table' },
        ],
        built_at: NOW,
        cached: false,
      });
    }
    if (/^\/api\/connections\/[^/?]+\/table\?/.test(url)) {
      log.tables.push(url);
      const name = new URLSearchParams(url.split('?')[1] ?? '').get('name') ?? '';
      const schema = new URLSearchParams(url.split('?')[1] ?? '').get('schema') ?? '';
      return json({
        schema,
        name,
        kind: 'table',
        columns: (name === 'parts' ? ['id', 'label', 'weight'] : ['id', 'name']).map((column) => ({
          name: column,
          type: 'text',
          nullable: true,
        })),
      });
    }
    return null;
  };

  return { handle, log, scripts };
}
