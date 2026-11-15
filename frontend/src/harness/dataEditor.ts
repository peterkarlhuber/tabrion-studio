/**
 * The harness's fake Data Editor server (scenario `data-editor`, STU-XDE-1): an unlocked PostgreSQL-like table whose
 * columns have the types the cell editors cover (boolean, enum, numeric, date, timestamptz, UUID, JSON, an int4
 * array, bytes, varchar, an identity key). Plans are answered with a preview and a run, and kept in `log.plans` for
 * e2e/data-editor-types.spec.ts. Like the real server, it refuses a value the page's checks let through: a `name` of
 * `refuse` is answered with `studio.data_editor_value_invalid` and the issue's position, no value.
 *
 * STU-XDE-2 (e2e/data-editor-states.spec.ts) adds the states the other specs do not reach, as options from the
 * harness's query: `locked` (no unlock yet: the unlock form, D-03, with the write profile `parts-w`; `prod` answers
 * the unlock with `studio.write_prod_locked`), `triggers` (the table has a trigger, a rule and row security: a notice,
 * not a block), `refuse` (the table route answers `studio.data_editor_column_incomparable`,
 * `studio.plan_target_partitioned`, `studio.plan_target_no_primary_key` or `studio.plan_target_invalid`) and
 * `conflict` (the run answers `studio.data_editor_conflict`).
 */

const COLUMNS = ['id', 'name', 'ok', 'mood', 'price', 'made', 'seen', 'tag', 'doc', 'nums', 'raw'];

const type = (name: string, kind: string, extra: Record<string, unknown> = {}) => ({
  name,
  kind,
  nullable: true,
  optional: true,
  read_only: false,
  array: false,
  ...extra,
});

const TABLE = {
  connection_id: 'c1',
  schema: 'app',
  table: 'parts',
  columns: COLUMNS,
  column_types: [
    type('id', 'int4', { nullable: false }),
    type('name', 'text', { length: 8, nullable: false, optional: false }),
    type('ok', 'boolean'),
    type('mood', 'enum', { values: ['ok', 'bad', 'unknown'] }),
    type('price', 'numeric', { precision: 6, scale: 2 }),
    type('made', 'date'),
    type('seen', 'timestamptz'),
    type('tag', 'uuid'),
    type('doc', 'json'),
    type('nums', 'int4', { array: true }),
    type('raw', 'bytes'),
  ],
  primary_key: ['id'],
  triggers: [],
  rules: [],
  row_security: false,
  max_changes: 1000,
};

const ROWS = [
  [
    1,
    'bolt',
    true,
    'ok',
    '1.50',
    '2024-01-02',
    '2024-01-02T03:04:05+00:00',
    'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11',
    { a: 1 },
    [1, 2],
    { base64: 'AQI=' },
  ],
  [2, 'nut', null, null, null, null, null, null, null, null, null],
  [3, 'washer', false, 'bad', '0.25', '2023-12-31', null, null, [], [], null],
];

const HASH = 'd'.repeat(64);

export interface DataEditorLog {
  plans: unknown[];
  executed: number;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

export interface DataEditorOptions {
  /** No unlock yet: the page starts with the unlock form. `prod` refuses the unlock as the server does for PROD. */
  locked?: 'yes' | 'prod' | null;
  /** The table has a trigger and a rule, and row security is on. */
  triggers?: boolean;
  /** The table route refuses the table with this code (without `studio.`). */
  refuse?: string | null;
  /** The run finds a row changed meanwhile. */
  conflict?: boolean;
}

const REFUSALS = new Set([
  'data_editor_column_incomparable',
  'plan_target_partitioned',
  'plan_target_no_primary_key',
  'plan_target_invalid',
]);

const problem = (code: string, status = 400) =>
  json({ code: `studio.${code}`, kind: 'request', status }, status);

export function createDataEditorServer(options: DataEditorOptions = {}): {
  fetch: typeof fetch;
  log: DataEditorLog;
} {
  const log: DataEditorLog = { plans: [], executed: 0 };
  let unlocked = !options.locked;
  const triggers = options.triggers
    ? [{ name: 'parts_audit', timing: 'AFTER', events: ['UPDATE', 'DELETE'], enabled: true }]
    : [];
  const rules = options.triggers ? [{ name: 'parts_log', event: 'INSERT', instead: false }] : [];
  const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const body: unknown = typeof init?.body === 'string' ? JSON.parse(init.body) : null;
    if (url === '/api/write/unlocks' && method === 'GET') {
      return json({
        unlocks: unlocked ? [{ connection_id: 'c1', write_profile_id: 'write:parts-w' }] : [],
      });
    }
    if (url === '/api/write/profiles' && method === 'GET') {
      return json({
        write_profiles: [
          {
            id: 'write:parts-w',
            name: 'parts-w',
            kind: 'write',
            engine: 'postgresql',
            read_profile_id: 'user:parts',
            secret: { confirmed: true, foreign: false },
          },
        ],
      });
    }
    if (url === '/api/write/unlocks' && method === 'POST') {
      if (options.locked === 'prod') return problem('write_prod_locked', 405);
      if ((body as { typed_name?: string }).typed_name !== 'parts-w') {
        return problem('unlock_name_mismatch');
      }
      unlocked = true;
      return json({ connection_id: 'c1', write_profile_id: 'write:parts-w' }, 201);
    }
    if (url === '/api/write/unlocks' && method === 'DELETE') {
      unlocked = false;
      return new Response(null, { status: 204 });
    }
    if (url === '/api/write/data-editor/table') {
      if (!unlocked) return problem('write_locked', 405);
      if (options.refuse && REFUSALS.has(options.refuse)) return problem(options.refuse);
      return json({ ...TABLE, triggers, rules, row_security: options.triggers === true });
    }
    if (url.startsWith('/api/connections/c1/rows?')) {
      const limit = Number(new URL(url, 'http://harness').searchParams.get('limit'));
      return json({
        connection_id: 'c1',
        schema: 'app',
        name: 'parts',
        limit,
        sort: null,
        desc: false,
        columns: COLUMNS.map((name) => ({ name, type: '' })),
        rows: ROWS,
        truncated_cells: [],
        page: 'p1',
        next: null,
        paging: 'primary_key',
      });
    }
    if (url === '/api/write/plans' && method === 'POST') {
      log.plans.push(body);
      type Sent = { op: string; new?: Record<string, unknown>; values?: Record<string, unknown> };
      const changes = (body as { request: { changes: Sent[] } }).request.changes;
      const refused = changes.findIndex(
        (change) => (change.new ?? change.values ?? {}).name === 'refuse',
      );
      if (refused >= 0) {
        return json(
          {
            code: 'studio.data_editor_value_invalid',
            kind: 'request',
            status: 400,
            issues: [{ pointer: `changes.${refused}.1`, code: 'value.not_allowed' }],
          },
          400,
        );
      }
      const destructive = changes.some((change) => change.op === 'delete');
      return json(
        {
          plan_hash: HASH,
          confirm_text: destructive ? 'parts' : 'parts-w',
          destructive,
          risks: destructive ? ['plan.destructive'] : [],
          steps: [],
          targets: [
            {
              schema: 'app',
              table: 'parts',
              triggers,
              rules,
            },
          ],
        },
        201,
      );
    }
    if (url === '/api/write/plans/confirm') return json({ plan_hash: HASH, confirmed: true });
    if (url === '/api/write/plans/execute') {
      if (options.conflict) return problem('data_editor_conflict');
      log.executed += 1;
      return json({ plan_hash: HASH, rows: { updated: 1 }, audit_pending: false });
    }
    if (url === '/api/write/plans' && method === 'DELETE')
      return new Response(null, { status: 204 });
    return json({ code: 'studio.not_found', kind: 'request', status: 404 }, 404);
  }) as typeof fetch;
  return { fetch: fetcher, log };
}
