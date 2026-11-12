/**
 * The data viewer's answers in the e2e harness (`scenario=explorer`) and the jsdom tests (STU-013 page), shaped like
 * the backend's (`services/viewer.py`): `GET /api/connections/{id}/rows` and `.../cell` for the explorer's fake
 * tables. Pages are cut by `limit`, sorted (NULLs last in both directions) and filtered like the server does; tokens
 * are opaque and kept for the last 64 pages. One value of `part` is longer than a page shows, so the page lists it in
 * `truncated_cells` and the cell route returns it whole. The table with the hostile name has no primary key (`rowid`
 * paging) and hostile values (U-1, T-07).
 */
import { HOSTILE_TABLE } from './explorer';

type Value = string | number | boolean | null;

interface FakeTable {
  connection: 'c-sqlite' | 'c-pg';
  schema: string;
  columns: { name: string; type: string }[];
  rows: Value[][];
  paging: 'primary_key' | 'rowid' | 'ctid' | 'offset';
}

/** The `name` of part 7 is this long; a page shows its first `PAGE_CHARS` characters. */
export const LONG_CHARS = 5000;
export const PAGE_CHARS = 200;
export const HOSTILE_VALUE = '<img src=x onerror=alert(1)>‮txt.exe';

const STATUSES = ['draft', 'released', 'blocked', 'obsolete'];

function parts(): Value[][] {
  const rows: Value[][] = [];
  for (let id = 1; id <= 250; id += 1) {
    rows.push([
      id,
      `P-${String(id).padStart(4, '0')}`,
      id === 7 ? `Long part ${'x'.repeat(LONG_CHARS - 10)}` : `Part ${id}`,
      (id % 5) + 1,
      STATUSES[id % 4] ?? 'draft',
      1 + (id % 3),
      `2026-01-${String((id % 28) + 1).padStart(2, '0')}T08:00:00`,
      id % 9 === 0 ? null : `2026-02-${String((id % 28) + 1).padStart(2, '0')}T09:30:00`,
    ]);
  }
  return rows;
}

function measurements(): Value[][] {
  const rows: Value[][] = [];
  for (let id = 1; id <= 1000; id += 1) {
    rows.push([
      id,
      (id % 12) + 1,
      `2026-03-01T00:${String(id % 60).padStart(2, '0')}:00+00:00`,
      (id * 0.25).toFixed(6),
      id % 7 === 0 ? null : id % 3,
    ]);
  }
  return rows;
}

export const VIEWER_TABLES: Record<string, FakeTable> = {
  'c-sqlite|part': {
    connection: 'c-sqlite',
    schema: 'main',
    columns: [
      { name: 'id', type: 'INTEGER' },
      { name: 'part_number', type: 'VARCHAR(40)' },
      { name: 'name', type: 'VARCHAR(200)' },
      { name: 'material_id', type: 'INTEGER' },
      { name: 'status', type: 'VARCHAR(16)' },
      { name: 'version', type: 'INTEGER' },
      { name: 'created_at', type: 'VARCHAR(32)' },
      { name: 'updated_at', type: 'VARCHAR(32)' },
    ],
    rows: parts(),
    paging: 'primary_key',
  },
  'c-sqlite|material': {
    connection: 'c-sqlite',
    schema: 'main',
    columns: [
      { name: 'id', type: 'INTEGER' },
      { name: 'code', type: 'VARCHAR(32)' },
      { name: 'name', type: 'VARCHAR(200)' },
      { name: 'created_at', type: 'VARCHAR(32)' },
    ],
    rows: ['Steel', 'Aluminium', 'Copper', 'Brass', 'Titanium'].map((name, index) => [
      index + 1,
      name.slice(0, 2).toUpperCase(),
      name,
      '2026-01-01T00:00:00',
    ]),
    paging: 'primary_key',
  },
  [`c-sqlite|${HOSTILE_TABLE}`]: {
    connection: 'c-sqlite',
    schema: 'main',
    columns: [{ name: 'body‮evil', type: 'TEXT' }],
    rows: [[HOSTILE_VALUE], ['line one\nline two'], [null]],
    paging: 'rowid',
  },
  'c-pg|measurement': {
    connection: 'c-pg',
    schema: 'public',
    columns: [
      { name: 'id', type: 'BIGINT' },
      { name: 'device_id', type: 'BIGINT' },
      { name: 'measured_at', type: 'TIMESTAMP WITH TIME ZONE' },
      { name: 'value', type: 'NUMERIC(18, 6)' },
      { name: 'quality', type: 'SMALLINT' },
    ],
    rows: measurements(),
    paging: 'primary_key',
  },
  'c-pg|device': {
    connection: 'c-pg',
    schema: 'public',
    columns: [
      { name: 'id', type: 'BIGINT' },
      { name: 'name', type: 'TEXT' },
    ],
    rows: Array.from({ length: 12 }, (_, index) => [index + 1, `Device ${index + 1}`]),
    paging: 'primary_key',
  },
};

interface Filter {
  column: string;
  op: string;
  value?: Value;
}

interface Page {
  key: string;
  sort: string | null;
  desc: boolean;
  filters: Filter[];
  limit: number;
  offset: number;
  /** The table's row numbers on this page. */
  rows: number[];
}

const OPS = new Set([
  'eq',
  'ne',
  'lt',
  'le',
  'gt',
  'ge',
  'contains',
  'starts_with',
  'is_null',
  'not_null',
]);

export interface ViewerLog {
  /** Every rows request: table, limit, sort, desc, filters (as sent) and after. */
  pages: {
    table: string;
    limit: number;
    sort: string | null;
    desc: boolean;
    filter: string | null;
    after: string | null;
  }[];
  cells: { page: string; row: number; column: string }[];
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const problem = (code: string, status: number) => json({ code, kind: 'request', status }, status);

function compare(a: Value, b: Value): number {
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b));
}

function matches(value: Value, filter: Filter): boolean {
  const wanted = filter.value ?? null;
  switch (filter.op) {
    case 'is_null':
      return value === null;
    case 'not_null':
      return value !== null;
    case 'contains':
      return value !== null && String(value).toLowerCase().includes(String(wanted).toLowerCase());
    case 'starts_with':
      return value !== null && String(value).toLowerCase().startsWith(String(wanted).toLowerCase());
    default: {
      if (value === null || wanted === null) return false;
      const order =
        typeof value === 'number' && !Number.isNaN(Number(wanted))
          ? value - Number(wanted)
          : String(value).localeCompare(String(wanted));
      return {
        eq: order === 0,
        ne: order !== 0,
        lt: order < 0,
        le: order <= 0,
        gt: order > 0,
        ge: order >= 0,
      }[filter.op as 'eq'];
    }
  }
}

/** A fake viewer: `answer(url)` returns the response for a viewer route, or null for any other request. */
export function createViewer(): { answer: (url: string) => Response | null; log: ViewerLog } {
  const pages = new Map<string, Page>();
  const log: ViewerLog = { pages: [], cells: [] };
  let next = 1;

  const keep = (page: Page): string => {
    const token = `vp${next++}`;
    pages.set(token, page);
    if (pages.size > 64) pages.delete(pages.keys().next().value as string);
    return token;
  };

  const answer = (url: string): Response | null => {
    const match = /^\/api\/connections\/(c-sqlite|c-pg)\/(rows|cell)\?(.*)$/.exec(url);
    if (match === null) return null;
    const [, connection, route, query] = match;
    const params = new URLSearchParams(query);
    const key = `${connection}|${params.get('name') ?? ''}`;
    const table = VIEWER_TABLES[key];
    if (table === undefined || params.get('schema') !== table.schema) {
      return problem('studio.object_not_found', 404);
    }
    const names = table.columns.map((column) => column.name);
    if (route === 'cell') {
      const token = params.get('page') ?? '';
      const row = Number(params.get('row'));
      const column = params.get('column') ?? '';
      log.cells.push({ page: token, row, column });
      const page = pages.get(token);
      if (page === undefined || page.key !== key) return problem('studio.viewer_page_unknown', 404);
      if (table.paging === 'offset') return problem('studio.viewer_cell_unavailable', 409);
      const index = names.indexOf(column);
      const number = page.rows[row];
      if (index < 0) return problem('studio.viewer_column_unknown', 400);
      if (number === undefined) return problem('studio.invalid_request', 400);
      const value = table.rows[number]?.[index] ?? null;
      const size = typeof value === 'string' ? new TextEncoder().encode(value).length : 8;
      return json({ value, size, truncated: false });
    }
    const after = params.get('after');
    const filterText = params.get('filter');
    log.pages.push({
      table: key,
      limit: Number(params.get('limit')),
      sort: params.get('sort'),
      desc: params.get('desc') === 'true',
      filter: filterText,
      after,
    });
    let base: Omit<Page, 'rows'>;
    if (after !== null) {
      const before = pages.get(after);
      if (before === undefined || before.key !== key) {
        return problem('studio.viewer_page_unknown', 404);
      }
      base = { ...before, offset: before.offset + before.rows.length };
    } else {
      const sort = params.get('sort');
      if (sort !== null && !names.includes(sort))
        return problem('studio.viewer_column_unknown', 400);
      let filters: Filter[] = [];
      if (filterText !== null) {
        try {
          filters = JSON.parse(filterText) as Filter[];
        } catch {
          return problem('studio.viewer_filter_invalid', 400);
        }
        if (!Array.isArray(filters) || filters.length > 20 || filters.some((f) => !OPS.has(f.op))) {
          return problem('studio.viewer_filter_invalid', 400);
        }
        if (filters.some((f) => !names.includes(f.column))) {
          return problem('studio.viewer_column_unknown', 400);
        }
      }
      base = {
        key,
        sort,
        desc: params.get('desc') === 'true',
        filters,
        limit: Number(params.get('limit') ?? 100),
        offset: 0,
      };
    }
    const sortIndex = base.sort === null ? -1 : names.indexOf(base.sort);
    const order = table.rows
      .map((_, number) => number)
      .filter((number) =>
        base.filters.every((filter) =>
          matches(table.rows[number]?.[names.indexOf(filter.column)] ?? null, filter),
        ),
      );
    if (sortIndex >= 0) {
      order.sort((a, b) => {
        const x = table.rows[a]?.[sortIndex] ?? null;
        const y = table.rows[b]?.[sortIndex] ?? null;
        if (x === null || y === null) return x === y ? a - b : x === null ? 1 : -1;
        return (base.desc ? -compare(x, y) : compare(x, y)) || a - b;
      });
    }
    const chosen = order.slice(base.offset, base.offset + base.limit);
    const more = order.length > base.offset + base.limit;
    const truncated: [number, number][] = [];
    const rows = chosen.map((number, index) =>
      (table.rows[number] ?? []).map((value, column) => {
        if (typeof value === 'string' && value.length > PAGE_CHARS) {
          truncated.push([index, column]);
          return value.slice(0, PAGE_CHARS);
        }
        return value;
      }),
    );
    const token = keep({ ...base, rows: chosen });
    return json({
      connection_id: connection,
      schema: table.schema,
      name: params.get('name'),
      columns: table.columns,
      rows,
      truncated_cells: truncated,
      truncated: null,
      page: token,
      next: more ? token : null,
      paging: table.paging,
      hint: table.paging === 'primary_key' ? null : 'no_primary_key',
      sort: base.sort,
      desc: base.desc,
      limit: base.limit,
    });
  };

  return { answer, log };
}
