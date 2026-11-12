/**
 * Drafts of the editor tabs in this browser (03 section 2 C: "drafts stored locally"): the title, the profile and
 * the SQL text of every tab, and which tab is open. Parameter values are never stored (08 section 5), and neither
 * are results (threat T-15).
 *
 * Browser storage belongs to the origin, and Studio's port changes with every start unless `--port` is given, so a
 * draft survives a reload but not always a restart. Keeping drafts in Studio's state folder (08 section 4.4 names the
 * state database) is docs/TODO.md TD-033. Every access is guarded: storage can be unavailable or full.
 */

export interface DraftTab {
  id: string;
  title: string;
  /** The profile the tab runs on; empty when none is chosen. */
  profileId: string;
  sql: string;
  /** The text as it was last saved as a file (D-06 asks when it differs). */
  savedSql: string;
}

export interface Drafts {
  tabs: DraftTab[];
  activeTab: string;
}

export const DRAFTS_KEY = 'tabrion-studio.editor-drafts';
/** The most tabs and the most text kept; a larger draft is not stored (the tab still works). */
export const MAX_TABS = 32;
export const MAX_DRAFT_CHARS = 512 * 1024;

function text(value: unknown, limit: number): string | null {
  return typeof value === 'string' && value.length <= limit ? value : null;
}

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
  if (!Array.isArray(tabs)) return null;
  const parsed = tabs.slice(0, MAX_TABS).flatMap((item: unknown): DraftTab[] => {
    if (typeof item !== 'object' || item === null) return [];
    const tab = item as Record<string, unknown>;
    const id = text(tab.id, 64);
    const title = text(tab.title, 128);
    const profileId = text(tab.profile_id, 512) ?? '';
    const sql = text(tab.sql, MAX_DRAFT_CHARS);
    const savedSql = text(tab.saved_sql, MAX_DRAFT_CHARS) ?? '';
    if (id === null || title === null || sql === null) return [];
    return [{ id, title, profileId, sql, savedSql }];
  });
  if (parsed.length === 0) return null;
  const active = parsed.some((tab) => tab.id === activeTab)
    ? (activeTab as string)
    : (parsed[0] as DraftTab).id;
  return { tabs: parsed, activeTab: active };
}

export function readDrafts(): Drafts | null {
  try {
    return parseDrafts(window.localStorage.getItem(DRAFTS_KEY));
  } catch {
    return null;
  }
}

export function writeDrafts(drafts: Drafts): void {
  const doc = {
    tabs: drafts.tabs.slice(0, MAX_TABS).map((tab) => ({
      id: tab.id,
      title: tab.title,
      profile_id: tab.profileId,
      sql: tab.sql.length <= MAX_DRAFT_CHARS ? tab.sql : '',
      saved_sql: tab.savedSql.length <= MAX_DRAFT_CHARS ? tab.savedSql : '',
    })),
    activeTab: drafts.activeTab,
  };
  try {
    window.localStorage.setItem(DRAFTS_KEY, JSON.stringify(doc));
  } catch {
    // Storage unavailable or full: the drafts live only in this page.
  }
}
