/**
 * The rows of the explorer tree (STU-010, 08 section 4.3): a flat list in display order, each with its level, its
 * position among its siblings and whether it is open, as the WAI-ARIA tree pattern needs them (`aria-level`,
 * `aria-posinset`, `aria-setsize`, `aria-expanded`). Flat, so that a virtualized list can render it later (TD-041).
 *
 * - PostgreSQL: schema → kind group (Tables, Views, …) → object; then the database's extensions.
 * - SQLite: the database file (its facts), then the kind groups of `main` (08: "node File instead of schemas").
 *
 * The filter (03 section 2 B: name prefix and kind) shows only the matching objects, with their groups open.
 */
import {
  OBJECT_KINDS,
  type Catalog,
  type CatalogObject,
  type ObjectKind,
} from '../../api/explorer';

export const GROUP_KINDS: readonly ObjectKind[] = [...OBJECT_KINDS, 'other'];

/** How many objects of one group are rendered (no virtualization yet, TD-041); the filter finds the others. */
export const MAX_GROUP_ITEMS = 500;
/** 08 section 4.3: from this many objects on, the page says that it loads names only. */
export const LARGE_CATALOG = 2000;

export type RowType = 'schema' | 'group' | 'object' | 'extensions' | 'file';

export interface TreeRow {
  id: string;
  type: RowType;
  level: number;
  /** The parent row's id, null on the first level. */
  parent: string | null;
  posinset: number;
  setsize: number;
  /** undefined: a leaf. */
  expanded?: boolean;
  /** Open because a filter is active: it cannot be closed, and ← goes to its parent (L-3 of stu-010-ui-review.md). */
  forced?: boolean;
  /** The schema of a schema, group or object row. */
  schema?: string;
  /** The kind of a group or object row. */
  kind?: ObjectKind;
  /** The object of an object row. */
  object?: CatalogObject;
  /** Objects in a schema or group row (all of them, also those not rendered). */
  count?: number;
  /** Objects of a group row that are not rendered (more than MAX_GROUP_ITEMS). */
  hidden?: number;
}

export interface Filter {
  query: string;
  kind: ObjectKind | 'all';
}

export const NO_FILTER: Filter = { query: '', kind: 'all' };

export function schemaId(schema: string): string {
  return JSON.stringify(['s', schema]);
}

export function groupId(schema: string, kind: ObjectKind): string {
  return JSON.stringify(['g', schema, kind]);
}

export function objectId(schema: string, name: string): string {
  return JSON.stringify(['o', schema, name]);
}

export const EXTENSIONS_ID = JSON.stringify(['x']);
export const FILE_ID = JSON.stringify(['f']);

export function filtering(filter: Filter): boolean {
  return filter.query.trim() !== '' || filter.kind !== 'all';
}

/** Name prefix, case-insensitive; with a dot, also a prefix of `schema.name`. */
export function matches(object: CatalogObject, filter: Filter): boolean {
  if (filter.kind !== 'all' && object.kind !== filter.kind) return false;
  const query = filter.query.trim().toLocaleLowerCase();
  if (query === '') return true;
  if (object.name.toLocaleLowerCase().startsWith(query)) return true;
  return (
    query.includes('.') && `${object.schema}.${object.name}`.toLocaleLowerCase().startsWith(query)
  );
}

/** SQLite's single schema `main` is not shown as a node of its own. */
export function flatSchema(catalog: Catalog): boolean {
  return catalog.engine === 'sqlite' && catalog.schemas.length === 1;
}

/** The rows open when a catalog is first shown: the first schema (`public` if there is one) and its tables. */
export function initialExpanded(catalog: Catalog): Set<string> {
  const first = catalog.schemas.includes('public') ? 'public' : catalog.schemas[0];
  if (first === undefined) return new Set();
  return new Set([schemaId(first), groupId(first, 'table')]);
}

export interface Rows {
  rows: TreeRow[];
  /** Objects that pass the filter. */
  matched: number;
  /** Whether a group shows fewer objects than it has. */
  truncated: boolean;
}

export function buildRows(catalog: Catalog, expanded: Set<string>, filter: Filter): Rows {
  const active = filtering(filter);
  const flat = flatSchema(catalog);
  const groupLevel = flat ? 1 : 2;
  const rows: TreeRow[] = [];
  let matched = 0;
  let truncated = false;

  const bySchema = new Map<string, Map<ObjectKind, CatalogObject[]>>();
  for (const schema of catalog.schemas) bySchema.set(schema, new Map());
  for (const object of catalog.objects) {
    if (!matches(object, filter)) continue;
    matched += 1;
    const groups = bySchema.get(object.schema);
    if (groups === undefined) continue;
    const items = groups.get(object.kind) ?? [];
    items.push(object);
    groups.set(object.kind, items);
  }

  const groupRows = (schema: string, parent: string | null): TreeRow[] => {
    const groups = bySchema.get(schema) ?? new Map<ObjectKind, CatalogObject[]>();
    const kinds = GROUP_KINDS.filter((kind) => (groups.get(kind)?.length ?? 0) > 0);
    const out: TreeRow[] = [];
    kinds.forEach((kind, index) => {
      const items = groups.get(kind) ?? [];
      const id = groupId(schema, kind);
      const open = active || expanded.has(id);
      const shown = items.slice(0, MAX_GROUP_ITEMS);
      out.push({
        id,
        type: 'group',
        level: groupLevel,
        parent,
        posinset: index + 1,
        setsize: kinds.length,
        expanded: open,
        forced: active,
        schema,
        kind,
        count: items.length,
        hidden: items.length - shown.length,
      });
      if (!open) return;
      if (shown.length < items.length) truncated = true;
      shown.forEach((object, position) => {
        out.push({
          id: objectId(object.schema, object.name),
          type: 'object',
          level: groupLevel + 1,
          parent: id,
          posinset: position + 1,
          setsize: shown.length,
          schema,
          kind,
          object,
        });
      });
    });
    return out;
  };

  if (flat) {
    if (!active) rows.push(fileRow());
    rows.push(...groupRows(catalog.schemas[0] as string, null));
    renumber(rows.filter((row) => row.level === 1));
    return { rows, matched, truncated };
  }

  for (const schema of catalog.schemas) {
    const groups = bySchema.get(schema);
    const total = [...(groups?.values() ?? [])].reduce((sum, items) => sum + items.length, 0);
    if (active && total === 0) continue;
    const id = schemaId(schema);
    const open = total > 0 && (active || expanded.has(id));
    rows.push({
      id,
      type: 'schema',
      level: 1,
      parent: null,
      posinset: 0,
      setsize: 0,
      expanded: total > 0 ? open : undefined,
      forced: active && total > 0,
      schema,
      count: total,
    });
    if (open) rows.push(...groupRows(schema, id));
  }
  if (!active && catalog.engine === 'postgresql') {
    rows.push({
      id: EXTENSIONS_ID,
      type: 'extensions',
      level: 1,
      parent: null,
      posinset: 0,
      setsize: 0,
    });
  }
  renumber(rows.filter((row) => row.level === 1));
  return { rows, matched, truncated };
}

function fileRow(): TreeRow {
  return { id: FILE_ID, type: 'file', level: 1, parent: null, posinset: 0, setsize: 0 };
}

function renumber(siblings: TreeRow[]): void {
  siblings.forEach((row, index) => {
    row.posinset = index + 1;
    row.setsize = siblings.length;
  });
}

/** The ids to open so that the object's row is rendered. */
export function pathTo(catalog: Catalog, object: CatalogObject): string[] {
  const ids = [groupId(object.schema, object.kind)];
  if (!flatSchema(catalog)) ids.unshift(schemaId(object.schema));
  return ids;
}
