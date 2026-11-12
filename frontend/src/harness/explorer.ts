/**
 * The explorer's answers in the e2e harness (`scenario=explorer`) and the jsdom tests (STU-010), shaped like the
 * backend's (`services/explorer.py`): the parts reference app on SQLite (`parts-dev`) and a measurement database on
 * PostgreSQL (`parts-pg`, `measure-central`). One table name carries a bidi override and a newline (U-1).
 */
export const HOSTILE_TABLE = 'notes‮txt.exe\nx';

export const SQLITE_CATALOG = {
  connection_id: 'c-sqlite',
  engine: 'sqlite',
  schemas: ['main'],
  objects: [
    { schema: 'main', name: 'alembic_version', kind: 'table' },
    { schema: 'main', name: 'material', kind: 'table' },
    { schema: 'main', name: HOSTILE_TABLE, kind: 'table' },
    { schema: 'main', name: 'part', kind: 'table' },
    { schema: 'main', name: 'part_relation', kind: 'table' },
    { schema: 'main', name: 'property', kind: 'table' },
    { schema: 'main', name: 'part_overview', kind: 'view' },
  ],
  built_at: 1_794_500_000,
  cached: false,
};

const column = (name: string, type: string, nullable = false, extra: object = {}) => ({
  name,
  type,
  nullable,
  default: null,
  comment: null,
  ...extra,
});

export const SQLITE_TABLES: Record<string, object> = {
  part: {
    connection_id: 'c-sqlite',
    schema: 'main',
    name: 'part',
    kind: 'table',
    columns: [
      column('id', 'INTEGER'),
      column('part_number', 'VARCHAR(40)'),
      column('name', 'VARCHAR(200)'),
      column('material_id', 'INTEGER'),
      column('status', 'VARCHAR(16)', false, { default: "'draft'" }),
      column('version', 'INTEGER', false, { default: '1' }),
      column('created_at', 'VARCHAR(32)'),
      column('updated_at', 'VARCHAR(32)'),
    ],
    primary_key: { name: 'pk_part', columns: ['id'] },
    foreign_keys: [
      {
        name: 'fk_part_material',
        columns: ['material_id'],
        referred_schema: 'main',
        referred_table: 'material',
        referred_columns: ['id'],
      },
    ],
    referenced_by: [
      {
        name: 'fk_part_relation_assembly',
        schema: 'main',
        table: 'part_relation',
        columns: ['assembly_id'],
        referred_columns: ['id'],
      },
      {
        name: 'fk_property_part',
        schema: 'main',
        table: 'property',
        columns: ['part_id'],
        referred_columns: ['id'],
      },
    ],
    unique_constraints: [{ name: 'uq_part_part_number', columns: ['part_number'] }],
    check_constraints: [
      { name: 'ck_part_status', sqltext: "status IN ('draft', 'released', 'blocked', 'obsolete')" },
      { name: 'ck_part_version', sqltext: 'version >= 1' },
    ],
    indexes: [{ name: 'ix_part_material_id', columns: ['material_id'], unique: false }],
    row_estimate: null,
  },
  material: {
    connection_id: 'c-sqlite',
    schema: 'main',
    name: 'material',
    kind: 'table',
    columns: [
      column('id', 'INTEGER'),
      column('code', 'VARCHAR(32)'),
      column('name', 'VARCHAR(200)'),
      column('created_at', 'VARCHAR(32)'),
    ],
    primary_key: { name: 'pk_material', columns: ['id'] },
    foreign_keys: [],
    referenced_by: [
      {
        name: 'fk_part_material',
        schema: 'main',
        table: 'part',
        columns: ['material_id'],
        referred_columns: ['id'],
      },
    ],
    unique_constraints: [{ name: 'uq_material_code', columns: ['code'] }],
    check_constraints: [{ name: 'ck_material_code', sqltext: 'length(code) BETWEEN 1 AND 32' }],
    indexes: [],
    row_estimate: null,
  },
  [HOSTILE_TABLE]: {
    connection_id: 'c-sqlite',
    schema: 'main',
    name: HOSTILE_TABLE,
    kind: 'table',
    columns: [
      column('body‮evil', 'TEXT', true, {
        default: `'${'x'.repeat(300)}'`,
        comment: '<img src=x onerror=alert(1)>',
      }),
    ],
    primary_key: { name: null, columns: [] },
    foreign_keys: [],
    referenced_by: [],
    unique_constraints: [],
    check_constraints: [{ name: null, sqltext: 'length(body) < 10⁦' }],
    indexes: [],
    row_estimate: null,
  },
};

export const SQLITE_INFO = {
  connection_id: 'c-sqlite',
  journal_mode: 'delete',
  user_version: 0,
  page_size: 4096,
  page_count: 13,
  foreign_keys: false,
  file_size: 53248,
  file_mode: '0600',
  tables: [
    { name: 'alembic_version', type: 'table', strict: false, without_rowid: false },
    { name: 'material', type: 'table', strict: false, without_rowid: false },
    { name: 'part', type: 'table', strict: true, without_rowid: false },
  ],
};

export const PG_CATALOG = {
  connection_id: 'c-pg',
  engine: 'postgresql',
  schemas: ['audit', 'public'],
  objects: [
    { schema: 'public', name: 'device', kind: 'table' },
    { schema: 'public', name: 'measurement', kind: 'table' },
    { schema: 'public', name: 'series', kind: 'table' },
    { schema: 'public', name: 'device_overview', kind: 'view' },
    { schema: 'public', name: 'measurement_daily', kind: 'materialized_view' },
    { schema: 'public', name: 'measurement_id_seq', kind: 'sequence' },
  ],
  built_at: 1_794_500_000,
  cached: true,
};

export const PG_TABLES: Record<string, object> = {
  measurement: {
    connection_id: 'c-pg',
    schema: 'public',
    name: 'measurement',
    kind: 'table',
    columns: [
      column('id', 'BIGINT', false, { default: "nextval('measurement_id_seq'::regclass)" }),
      column('device_id', 'BIGINT'),
      column('measured_at', 'TIMESTAMP WITH TIME ZONE'),
      column('value', 'NUMERIC(18, 6)', false, { comment: 'In the unit of the series.' }),
      column('quality', 'SMALLINT', true, { default: '0' }),
    ],
    primary_key: { name: 'measurement_pkey', columns: ['id'] },
    foreign_keys: [
      {
        name: 'measurement_device_id_fkey',
        columns: ['device_id'],
        referred_schema: 'public',
        referred_table: 'device',
        referred_columns: ['id'],
      },
    ],
    referenced_by: [],
    unique_constraints: [],
    check_constraints: [],
    indexes: [
      { name: 'measurement_device_time', columns: ['device_id', 'measured_at'], unique: true },
    ],
    row_estimate: 4_200_000,
  },
  device: {
    connection_id: 'c-pg',
    schema: 'public',
    name: 'device',
    kind: 'table',
    columns: [column('id', 'BIGINT'), column('name', 'TEXT')],
    primary_key: { name: 'device_pkey', columns: ['id'] },
    foreign_keys: [],
    referenced_by: [
      {
        name: 'measurement_device_id_fkey',
        schema: 'public',
        table: 'measurement',
        columns: ['device_id'],
        referred_columns: ['id'],
      },
    ],
    unique_constraints: [],
    check_constraints: [],
    indexes: [],
    row_estimate: 12,
  },
  measurement_id_seq: {
    connection_id: 'c-pg',
    schema: 'public',
    name: 'measurement_id_seq',
    kind: 'sequence',
  },
};

export const PG_EXTENSIONS = {
  connection_id: 'c-pg',
  extensions: [
    {
      name: 'pg_trgm',
      version: '1.5',
      schema: 'public',
      default_version: '1.6',
      update_available: true,
    },
    {
      name: 'plpgsql',
      version: '1.0',
      schema: 'pg_catalog',
      default_version: '1.0',
      update_available: false,
    },
  ],
};

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const problem = (code: string, status: number) => json({ code, kind: 'not_found', status }, status);

/** The explorer's answer to one request, or null when the request is not the explorer's. */
export function explorerAnswer(url: string, method: string, body?: string): Response | null {
  if (url === '/api/connections' && method === 'POST') {
    const sqlite = body?.includes('"project:parts-dev"') ?? false;
    return json({ connection_id: sqlite ? 'c-sqlite' : 'c-pg' }, 201);
  }
  const match = /^\/api\/connections\/(c-sqlite|c-pg)\/([a-z-]+)(?:\?(.*))?$/.exec(url);
  if (match === null || method !== 'GET') return null;
  const [, connection, route, query] = match;
  const sqlite = connection === 'c-sqlite';
  switch (route) {
    case 'catalog':
      return json(sqlite ? SQLITE_CATALOG : PG_CATALOG);
    case 'table': {
      const params = new URLSearchParams(query ?? '');
      const tables = sqlite ? SQLITE_TABLES : PG_TABLES;
      const name = params.get('name') ?? '';
      return name in tables ? json(tables[name]) : problem('studio.object_not_found', 404);
    }
    case 'sqlite-info':
      return sqlite ? json(SQLITE_INFO) : problem('studio.explorer_engine', 409);
    case 'extensions':
      return sqlite ? problem('studio.explorer_engine', 409) : json(PG_EXTENSIONS);
    default:
      return null;
  }
}
