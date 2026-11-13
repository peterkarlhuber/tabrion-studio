/**
 * A statement another page hands to the SQL editor (the explorer's "SELECT … LIMIT 100 in the SQL editor", 08
 * section 4.3, TD-041). The page sets it and navigates to `/sql`; the editor takes it once, when it has read its
 * drafts, and opens it in a new tab on the given profile. Nothing runs by itself: the user runs it, and D-02 is asked
 * there as for any run. It lives in this page's memory only (never in storage), so a reload does not repeat it.
 */
import type { Engine } from '../../api/explorer';

export interface Handoff {
  profileId: string;
  title: string;
  sql: string;
  /** Said in the editor's notice when the tab opens (a statement the history kept only in part). */
  notice?: string;
}

let pending: Handoff | null = null;

export function handToEditor(handoff: Handoff): void {
  pending = handoff;
}

/** The statement handed over, once; null when there is none. */
export function takeHandoff(): Handoff | null {
  const handoff = pending;
  pending = null;
  return handoff;
}

/** An identifier in double quotes, a `"` inside doubled (PostgreSQL and SQLite). */
export function quoteIdentifier(name: string): string {
  return `"${name.replaceAll('"', '""')}"`;
}

/** `SELECT * FROM <object> LIMIT 100;` for an object of the catalog; SQLite's `main` schema is left out. */
export function selectStatement(engine: Engine, schema: string, name: string, limit = 100): string {
  const object =
    engine === 'sqlite' && schema === 'main'
      ? quoteIdentifier(name)
      : `${quoteIdentifier(schema)}.${quoteIdentifier(name)}`;
  return `SELECT * FROM ${object} LIMIT ${limit};`;
}
