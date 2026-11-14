/**
 * Drafts of the editor tabs (03 section 2 C, 08 section 4.4): the title, the profile and the SQL text of every tab,
 * and which tab is open. Parameter values are never stored (08 section 5), and neither are results (threat T-15).
 *
 * TD-033 / review F-5: the drafts live in Studio's state database (`state.sqlite`, 0600) behind the authenticated
 * API, `GET` and `PUT /api/drafts` (docs/architecture/history-and-scripts.md, "Editor drafts"), one document per
 * project. They used to live in `localStorage`, which belongs to the port-scoped origin `http://127.0.0.1:<port>`, so
 * a later program on the same port in the same browser profile could read them. Nothing is written to browser storage
 * any more. Drafts an earlier version left there for this project (its own key and the unscoped key) are read once
 * (`readLegacyDrafts`), saved to the server with the page's first save, and then those two keys are removed
 * (`clearLegacyDrafts`). Other projects' keys stay until that project's editor moves them; while the server cannot
 * keep the drafts, or not all of them fit its caps, the keys stay too, so that nothing is lost.
 *
 * Two pages of one project merge by tab on the server: a save names every tab id the page has had (`known`), so the
 * server keeps the tabs another page added and drops only the tabs this page closed.
 */
import { read } from '../../api/explorer';
import { send, type Result } from '../../api/profiles';

export interface DraftTab {
  id: string;
  title: string;
  /** The profile the tab runs on; empty when none is chosen. */
  profileId: string;
  sql: string;
  /** The text as it was last saved as a file (D-06 asks when it differs). */
  savedSql: string;
  /** STU-020: the project script the tab was opened from or saved to (relative, `/`), if any. */
  scriptPath?: string;
  /** The SHA-256 of that script as last read or written: a save is bound to it (`studio.script_stale`). */
  scriptDigest?: string;
  /** The script's lines end in CRLF on disk: the editor shows `\n`, and a save writes CRLF back. */
  scriptCrlf?: boolean;
}

export interface Drafts {
  tabs: DraftTab[];
  activeTab: string;
}

/** The prefix of the browser-storage keys of earlier versions (read once for the migration, then removed). */
export const DRAFTS_KEY = 'tabrion-studio.editor-drafts';

/** A short hash of a text (53-bit cyrb53): the key names the project without holding its path. */
function hash(text: string): string {
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ code, 2654435761);
    h2 = Math.imul(h2 ^ code, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
}

/** The browser-storage key an earlier version kept a project's drafts under (L-5); the unscoped key without one. */
export function draftsKey(projectPath: string): string {
  return projectPath === '' ? DRAFTS_KEY : `${DRAFTS_KEY}.${hash(projectPath)}`;
}

/** The server's caps (`services/drafts.py`): tabs, one text, every text together. */
export const MAX_TABS = 32;
export const MAX_DRAFT_CHARS = 512 * 1024;
export const MAX_TOTAL_CHARS = 2 * 1024 * 1024;
/** The tab ids a save names as known (the server's cap). */
export const MAX_KNOWN = 1024;

function text(value: unknown, limit: number): string | null {
  return typeof value === 'string' && value.length <= limit ? value : null;
}

function parseTabs(tabs: unknown, activeTab: unknown): Drafts | null {
  if (!Array.isArray(tabs)) return null;
  const parsed = tabs.slice(0, MAX_TABS).flatMap((item: unknown): DraftTab[] => {
    if (typeof item !== 'object' || item === null) return [];
    const tab = item as Record<string, unknown>;
    const id = text(tab.id, 64);
    const title = text(tab.title, 128);
    const profileId = text(tab.profile_id, 512) ?? '';
    const sql = text(tab.sql, MAX_DRAFT_CHARS);
    const savedSql = text(tab.saved_sql, MAX_DRAFT_CHARS) ?? '';
    if (id === null || id === '' || title === null || sql === null) return [];
    const scriptPath = text(tab.script_path, 512);
    const scriptDigest =
      typeof tab.script_digest === 'string' && /^[0-9a-f]{64}$/.test(tab.script_digest)
        ? tab.script_digest
        : null;
    return [
      {
        id,
        title,
        profileId,
        sql,
        savedSql,
        ...(scriptPath !== null && scriptPath !== '' && scriptDigest !== null
          ? { scriptPath, scriptDigest, ...(tab.script_crlf === true ? { scriptCrlf: true } : {}) }
          : {}),
      },
    ];
  });
  if (parsed.length === 0) return null;
  const active = parsed.some((tab) => tab.id === activeTab)
    ? (activeTab as string)
    : (parsed[0] as DraftTab).id;
  return { tabs: parsed, activeTab: active };
}

/** Drafts as an earlier version stored them in the browser (`{tabs, activeTab}` as JSON text). */
export function parseDrafts(raw: string | null): Drafts | null {
  if (raw === null) return null;
  let doc: unknown;
  try {
    doc = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof doc !== 'object' || doc === null) return null;
  const { tabs, activeTab } = doc as Record<string, unknown>;
  return parseTabs(tabs, activeTab);
}

/** The answer of `GET /api/drafts`: `{drafts: {tabs, active_tab} | null}`; null when it has another shape. */
export function parseDraftsAnswer(body: unknown): { drafts: Drafts | null } | null {
  if (typeof body !== 'object' || body === null || !('drafts' in body)) return null;
  const drafts = (body as { drafts: unknown }).drafts;
  if (drafts === null) return { drafts: null };
  if (typeof drafts !== 'object') return null;
  const { tabs, active_tab: activeTab } = drafts as Record<string, unknown>;
  return { drafts: parseTabs(tabs, activeTab) };
}

/** A lone UTF-16 surrogate (a broken paste, a text cut inside a pair). */
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g;

/**
 * The text with each lone surrogate replaced by U+FFFD, as `String.prototype.toWellFormed` does: the server refuses
 * a text that is not valid Unicode (it cannot be stored as UTF-8), and the length stays the same.
 */
export function wellFormed(text: string): string {
  return text.replace(LONE_SURROGATE, '\uFFFD');
}

/**
 * The document `PUT /api/drafts` takes: the known fields of each tab and nothing else, within the server's caps. A
 * text over the cap of one text is left out (the tab still works), and while all texts together are over their cap,
 * the largest is left out first.
 */
export function draftsDocument(drafts: Drafts, known?: ReadonlySet<string>) {
  const tabs = drafts.tabs.slice(0, MAX_TABS).map((tab) => ({
    id: wellFormed(tab.id),
    title: wellFormed(tab.title),
    profile_id: wellFormed(tab.profileId),
    sql: tab.sql.length <= MAX_DRAFT_CHARS ? wellFormed(tab.sql) : '',
    saved_sql: tab.savedSql.length <= MAX_DRAFT_CHARS ? wellFormed(tab.savedSql) : '',
    ...(tab.scriptPath !== undefined && tab.scriptDigest !== undefined
      ? {
          script_path: wellFormed(tab.scriptPath),
          script_digest: tab.scriptDigest,
          ...(tab.scriptCrlf === true ? { script_crlf: true } : {}),
        }
      : {}),
  }));
  let total = tabs.reduce((sum, tab) => sum + tab.sql.length + tab.saved_sql.length, 0);
  while (total > MAX_TOTAL_CHARS) {
    let largest: { tab: (typeof tabs)[number]; field: 'sql' | 'saved_sql' } | null = null;
    for (const tab of tabs) {
      for (const field of ['sql', 'saved_sql'] as const) {
        if (largest === null || tab[field].length > largest.tab[largest.field].length) {
          largest = { tab, field };
        }
      }
    }
    if (largest === null) break;
    total -= largest.tab[largest.field].length;
    largest.tab[largest.field] = '';
  }
  return {
    tabs,
    active_tab: drafts.activeTab,
    ...(known !== undefined ? { known: [...known].slice(-MAX_KNOWN) } : {}),
  };
}

/**
 * The draft saves of this page, one after the other, for every editor view the page mounts (QA-S1 S1-15): a later
 * save never lands before an earlier one, and an editor that mounts again (back from the history, the explorer) reads
 * the drafts only after the saves of the editor it replaces have landed (`draftSavesSettled`). A per-view queue let
 * the new view's `GET /api/drafts` overtake the old view's last `PUT`, so it read drafts without that change.
 */
let draftSaves: Promise<unknown> = Promise.resolve();

/**
 * How long one draft save may take before it is aborted and the next save goes ahead (review M1 of QA-S1): a
 * `PUT /api/drafts` the server never answers must not hold back every later save of the page until it reloads. An
 * aborted save counts as a failed one (`saveDrafts` returns false).
 */
export const DRAFT_SAVE_TIMEOUT_MS = 10_000;

/**
 * Queue a save after the saves queued before it; a failed save does not stop the later ones. The save gets a signal
 * that aborts after `DRAFT_SAVE_TIMEOUT_MS`, and the queue goes on at that time even if the save does not stop.
 */
export function queueDraftSave(save: (signal: AbortSignal) => Promise<unknown>): void {
  draftSaves = draftSaves
    .then(() => {
      const controller = new AbortController();
      let timer: ReturnType<typeof setTimeout> | undefined;
      const limit = new Promise<void>((resolve) => {
        timer = setTimeout(() => {
          controller.abort();
          resolve();
        }, DRAFT_SAVE_TIMEOUT_MS);
      });
      return Promise.race([save(controller.signal), limit]).finally(() => clearTimeout(timer));
    })
    .catch(() => undefined);
}

/** How long a new editor waits at most for the saves before it reads the drafts (a save the server never answers). */
export const DRAFT_SAVES_WAIT_MS = 10_000;

/**
 * Resolves when every save queued so far has landed (or failed), or after `DRAFT_SAVES_WAIT_MS`: a save the server
 * never answers must not keep the editor from opening.
 */
export function draftSavesSettled(): Promise<void> {
  return new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, DRAFT_SAVES_WAIT_MS);
    void draftSaves.then(() => {
      clearTimeout(timer);
      resolve();
    });
  });
}

/** This project's drafts from the server; `null` when it keeps none. */
export async function fetchDrafts(fetcher: typeof fetch): Promise<Result<Drafts | null>> {
  const result = await read(fetcher, '/api/drafts', parseDraftsAnswer);
  return result.ok ? { ok: true, value: result.value.drafts } : result;
}

/**
 * Store the drafts on the server. With `known` (every tab id this page has had), the stored tabs this page never had
 * are kept after its own: another page of the same project added them. False when the server did not keep them, or
 * `signal` aborted the request (`queueDraftSave`'s time limit).
 */
export async function saveDrafts(
  fetcher: typeof fetch,
  drafts: Drafts,
  known?: ReadonlySet<string>,
  signal?: AbortSignal,
): Promise<boolean> {
  const result = await send<unknown>(
    fetcher,
    'PUT',
    '/api/drafts',
    draftsDocument(drafts, known),
    signal,
  );
  return result.ok;
}

// --- browser storage of earlier versions (migration only) ----------------------------------------------------------

function legacyKeys(): string[] {
  try {
    const keys: string[] = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const key = window.localStorage.key(i);
      if (key !== null && (key === DRAFTS_KEY || key.startsWith(`${DRAFTS_KEY}.`))) keys.push(key);
    }
    return keys;
  } catch {
    return [];
  }
}

/** The keys of this project's drafts of an earlier version: its own and the unscoped one. */
function projectKeys(projectPath: string): string[] {
  return projectPath === '' ? [DRAFTS_KEY] : [draftsKey(projectPath), DRAFTS_KEY];
}

/** Whether an earlier version left drafts of this project in this browser's storage. */
export function hasLegacyDrafts(projectPath: string): boolean {
  const keys = legacyKeys();
  return projectKeys(projectPath).some((key) => keys.includes(key));
}

/**
 * The drafts an earlier version kept in this browser for this project: its own key, then the unscoped key (written
 * while the project's folder was not known). Only read: `clearLegacyDrafts` removes them once the server has them.
 * The two keys together can have more than `MAX_TABS` tabs: they are all returned, so that `legacyKept` sees every
 * one of them and both keys stay when they do not fit (review delta N-2).
 */
export function readLegacyDrafts(projectPath: string): Drafts | null {
  const stored = (key: string) => {
    try {
      return parseDrafts(window.localStorage.getItem(key));
    } catch {
      return null;
    }
  };
  const own = projectPath === '' ? null : stored(draftsKey(projectPath));
  return unionDrafts(own, stored(DRAFTS_KEY));
}

/**
 * Remove this project's draft keys of earlier versions (its own and the unscoped one) from this browser's storage.
 * Other projects' keys stay: their folder cannot be told from the key's hash, so only that project's editor can move
 * them to the server (review finding 3).
 */
export function clearLegacyDrafts(projectPath: string): void {
  for (const key of projectKeys(projectPath)) {
    try {
      window.localStorage.removeItem(key);
    } catch {
      // Storage unavailable: nothing to remove.
    }
  }
}

/**
 * Whether the drafts of an earlier version are all kept in `merged`, the document the page saves: every one of their
 * tabs, with no text left out by the caps (`draftsDocument`). Only then are their keys removed.
 */
export function legacyKept(merged: Drafts, legacy: Drafts): boolean {
  const ids = new Set(merged.tabs.slice(0, MAX_TABS).map((tab) => tab.id));
  const total = merged.tabs.reduce((sum, tab) => sum + tab.sql.length + tab.savedSql.length, 0);
  return legacy.tabs.every((tab) => ids.has(tab.id)) && total <= MAX_TOTAL_CHARS;
}

/** `first`'s tabs, then `second`'s tabs that `first` does not have (by id), all of them. */
function unionDrafts(first: Drafts | null, second: Drafts | null): Drafts | null {
  if (first === null) return second;
  if (second === null) return first;
  const ids = new Set(first.tabs.map((tab) => tab.id));
  return {
    tabs: [...first.tabs, ...second.tabs.filter((tab) => !ids.has(tab.id))],
    activeTab: first.activeTab,
  };
}

/** `first`'s tabs, then `second`'s tabs that `first` does not have (by id), within the tab cap. */
export function mergeDrafts(first: Drafts | null, second: Drafts | null): Drafts | null {
  const union = unionDrafts(first, second);
  if (union === null || union.tabs.length <= MAX_TABS) return union;
  return { ...union, tabs: union.tabs.slice(0, MAX_TABS) };
}
