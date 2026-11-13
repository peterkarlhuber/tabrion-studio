/**
 * Completion from the explorer's catalog (STU-020; 03 section 2 C "Autocomplete", 08 section 4.4).
 *
 * CodeMirror's SQL language completes the keywords of the dialect, and with a namespace the schemas, tables and
 * views. The namespace comes from the catalog the explorer reads (`GET /api/connections/{id}/catalog`); the page never
 * reads a database for completion on its own beyond that. On a PROD profile the page asks only for a catalog that is
 * already loaded (`cached_only`, 03 section 5.4: no large catalog without a click) and reads it in full only after
 * the user's click. Columns come from a table's detail when the user types `table.`, on DEV and TEST profiles only.
 */
import type { Completion } from '@codemirror/autocomplete';
import type { SQLNamespace } from '@codemirror/lang-sql';

import type { Catalog, CatalogObject } from '../../api/explorer';
import type { Dialect } from './sqlScan';

/** The kinds that have columns to complete. */
const RELATIONS = new Set(['table', 'view', 'materialized_view', 'foreign_table']);

/** The most objects a namespace takes: a larger catalog completes its first ones (names sorted by the server). */
export const MAX_COMPLETION_OBJECTS = 5_000;

export function defaultSchemaOf(dialect: Dialect): string {
  return dialect === 'sqlite' ? 'main' : 'public';
}

export function columnKey(object: { schema: string; name: string }): string {
  return `${object.schema}\u0000${object.name}`;
}

/** The namespace for CodeMirror: schema, then table or view, then the columns known so far. */
export function namespaceOf(
  catalog: Catalog,
  columns: ReadonlyMap<string, readonly string[]>,
): SQLNamespace {
  const tree: Record<string, Record<string, readonly Completion[]>> = {};
  for (const schema of catalog.schemas) tree[schema] = {};
  for (const object of catalog.objects.slice(0, MAX_COMPLETION_OBJECTS)) {
    if (!RELATIONS.has(object.kind)) continue;
    const schema = (tree[object.schema] ??= {});
    schema[object.name] = (columns.get(columnKey(object)) ?? []).map((name) => ({
      label: name,
      type: 'property',
    }));
  }
  return tree;
}

/** `name.` (and what follows it) right before the cursor: the table or view whose columns to complete. */
export function qualifierBefore(text: string): { qualifier: string; from: number } | null {
  const match = /(?:^|[^\w$."])([A-Za-z_][\w$]*)\.([\w$]*)$/.exec(text);
  if (match === null) return null;
  const qualifier = match[1] as string;
  const rest = match[2] as string;
  return { qualifier, from: text.length - rest.length };
}

/**
 * The relation a qualifier names: the default schema first, then any schema. Unquoted names fold to lower case in
 * PostgreSQL; SQLite compares names without case.
 */
export function relationNamed(
  catalog: Catalog,
  qualifier: string,
  dialect: Dialect,
): CatalogObject | null {
  const relations = catalog.objects.filter((object) => RELATIONS.has(object.kind));
  const same = (name: string) =>
    dialect === 'sqlite'
      ? name.toLowerCase() === qualifier.toLowerCase()
      : name === qualifier || name === qualifier.toLowerCase();
  const preferred = defaultSchemaOf(dialect);
  return (
    relations.find((object) => object.schema === preferred && same(object.name)) ??
    relations.find((object) => same(object.name)) ??
    null
  );
}
