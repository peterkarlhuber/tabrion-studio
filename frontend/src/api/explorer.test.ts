/** STU-010: the explorer's parsers fail closed, and its requests only read. */
import {
  PG_DDL,
  PG_EXTENSIONS,
  PG_POLICIES,
  PG_TABLES,
  SQLITE_CATALOG,
  SQLITE_COUNTS,
  SQLITE_INFO,
} from '../harness/explorer';
import {
  count,
  epochMs,
  fetchCatalog,
  fetchDdl,
  fetchPolicies,
  fetchRowCount,
  fetchTable,
  parseCatalog,
  parseDdl,
  parseExtensions,
  parsePolicies,
  parseRowCount,
  parseSqliteInfo,
  parseTable,
} from './explorer';

describe('parseCatalog', () => {
  it('reads the backend shape', () => {
    const catalog = parseCatalog(SQLITE_CATALOG);
    expect(catalog?.engine).toBe('sqlite');
    expect(catalog?.schemas).toEqual(['main']);
    expect(catalog?.objects).toHaveLength(7);
    expect(catalog?.builtAt).toBe(1_794_500_000_000);
    expect(catalog?.cached).toBe(false);
  });

  it.each([
    ['not an object', []],
    ['no engine', { ...SQLITE_CATALOG, engine: 'mysql' }],
    ['no object list', { ...SQLITE_CATALOG, objects: null }],
    ['no schema list', { ...SQLITE_CATALOG, schemas: 'main' }],
    [
      'an object without a name',
      { ...SQLITE_CATALOG, objects: [{ schema: 'main', kind: 'table' }] },
    ],
    ['an object without a schema', { ...SQLITE_CATALOG, objects: [{ name: 'x', kind: 'table' }] }],
  ])('refuses an answer with %s', (_, body) => {
    expect(parseCatalog(body)).toBeNull();
  });

  it('keeps an unknown kind as "other" and lists a schema the server left out', () => {
    const catalog = parseCatalog({
      ...SQLITE_CATALOG,
      schemas: [],
      objects: [{ schema: 'main', name: 'x', kind: 'index' }],
    });
    expect(catalog?.objects).toEqual([{ schema: 'main', name: 'x', kind: 'other' }]);
    expect(catalog?.schemas).toEqual(['main']);
  });

  it('keeps every object, also past 5000: nothing is cut while reading (C-2)', () => {
    const objects = Array.from({ length: 6000 }, (_, i) => ({
      schema: 'main',
      name: `t${i}`,
      kind: 'table',
    }));
    const catalog = parseCatalog({ ...SQLITE_CATALOG, objects });
    expect(catalog?.objects).toHaveLength(6000);
    expect(catalog?.objects.at(-1)?.name).toBe('t5999');
  });
});

describe('numbers and times', () => {
  it.each([
    [0, 0],
    [12, 12],
    [-1, null],
    [1.5, null],
    [Number.MAX_SAFE_INTEGER + 2, null],
    ['12', null],
    [null, null],
  ])('count(%s) is %s', (value, expected) => {
    expect(count(value)).toBe(expected);
  });

  it.each([
    [1_794_500_000, 1_794_500_000_000],
    [1e15, null], // past what Date can show (C-1 of stu-014-ui-review.md)
    [Number.POSITIVE_INFINITY, null],
    [Number.NaN, null],
    [0, null],
    [-5, null],
    ['1794500000', null],
  ])('epochMs(%s) is %s', (value, expected) => {
    expect(epochMs(value)).toBe(expected);
  });
});

describe('parseTable', () => {
  it('reads the backend shape', () => {
    const detail = parseTable(PG_TABLES.measurement);
    expect(detail?.columns.map((c) => c.name)).toEqual([
      'id',
      'device_id',
      'measured_at',
      'value',
      'quality',
    ]);
    expect(detail?.columns[4]).toEqual({
      name: 'quality',
      type: 'SMALLINT',
      nullable: true,
      default: '0',
      comment: null,
    });
    expect(detail?.primaryKey).toEqual({ name: 'measurement_pkey', columns: ['id'] });
    expect(detail?.foreignKeys[0]?.referredTable).toBe('device');
    expect(detail?.indexes[0]).toEqual({
      name: 'measurement_device_time',
      columns: ['device_id', 'measured_at'],
      unique: true,
    });
    expect(detail?.rowEstimate).toBe(4_200_000);
  });

  it('a sequence needs no columns; any other object does', () => {
    expect(parseTable(PG_TABLES.measurement_id_seq)?.kind).toBe('sequence');
    expect(parseTable({ schema: 'public', name: 'x', kind: 'table' })).toBeNull();
    expect(parseTable({ name: 'x', kind: 'table', columns: [] })).toBeNull();
  });

  it('a value of the wrong type reads as unknown, never as a guess', () => {
    const detail = parseTable({
      schema: 'public',
      name: 'x',
      kind: 'table',
      columns: [{ name: 'a', type: 5, nullable: 'yes', default: 0 }, { type: 'TEXT' }],
      indexes: [{ name: 'i', columns: ['a', null], unique: 'no' }],
      row_estimate: '12',
    });
    expect(detail?.columns).toEqual([
      { name: 'a', type: null, nullable: null, default: null, comment: null },
    ]);
    expect(detail?.indexes).toEqual([{ name: 'i', columns: ['a'], unique: null }]);
    expect(detail?.rowEstimate).toBeNull();
  });
});

describe('parseExtensions and parseSqliteInfo', () => {
  it('read the backend shapes', () => {
    expect(parseExtensions(PG_EXTENSIONS)?.[0]).toEqual({
      name: 'pg_trgm',
      version: '1.5',
      schema: 'public',
      defaultVersion: '1.6',
      updateAvailable: true,
    });
    const info = parseSqliteInfo(SQLITE_INFO);
    expect(info?.fileMode).toBe('0600');
    expect(info?.tables.find((t) => t.name === 'part')?.strict).toBe(true);
  });

  it('refuse what they cannot read', () => {
    expect(parseExtensions({ extensions: 'x' })).toBeNull();
    expect(parseSqliteInfo('x')).toBeNull();
    const info = parseSqliteInfo({
      ...SQLITE_INFO,
      file_mode: '0600; rm',
      page_size: -1,
      file_size: 1.5,
    });
    expect(info?.fileMode).toBeNull();
    expect(info?.pageSize).toBeNull();
    expect(info?.fileSize).toBeNull();
  });
});

describe('requests', () => {
  it('only GET, with the session headers, and the query form for the table (a name may hold "/")', async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init });
      return new Response('{}', { status: 200 });
    }) as typeof fetch;
    await fetchTable(fetcher, 'c 1', 'my/schema', 'a/b&c');
    await fetchCatalog(fetcher, 'c1', true);
    expect(calls.map((c) => c.url)).toEqual([
      '/api/connections/c%201/table?schema=my%2Fschema&name=a%2Fb%26c',
      '/api/connections/c1/catalog?refresh=true',
    ]);
    for (const call of calls) {
      expect(call.init?.method).toBeUndefined();
      expect(call.init?.credentials).toBe('same-origin');
    }
  });

  it('an answer for another object or another connection is refused (I-1)', async () => {
    const answer = (body: unknown) =>
      (async () => new Response(JSON.stringify(body), { status: 200 })) as unknown as typeof fetch;
    const invalid = { ok: false, problem: { code: 'studio.answer_invalid' } };
    expect(
      await fetchTable(answer(PG_TABLES.device), 'c-pg', 'public', 'measurement'),
    ).toMatchObject(invalid);
    expect(await fetchTable(answer(PG_TABLES.device), 'c-pg', 'audit', 'device')).toMatchObject(
      invalid,
    );
    expect(await fetchTable(answer(PG_TABLES.device), 'c-pg', 'public', 'device')).toMatchObject({
      ok: true,
    });
    expect(await fetchCatalog(answer(SQLITE_CATALOG), 'c-pg')).toMatchObject(invalid);
    expect(await fetchCatalog(answer(SQLITE_CATALOG), 'c-sqlite')).toMatchObject({ ok: true });
    const { connection_id: _, ...anonymous } = SQLITE_CATALOG;
    expect(await fetchCatalog(answer(anonymous), 'c-pg')).toMatchObject({ ok: true });
  });

  it('an answer that is not JSON or not the shape is answer_invalid; a refusal keeps its code', async () => {
    const answer = (body: string, status = 200) =>
      (async () => new Response(body, { status })) as unknown as typeof fetch;
    expect(await fetchCatalog(answer('not json'), 'c1')).toMatchObject({
      ok: false,
      problem: { code: 'studio.answer_invalid' },
    });
    expect(await fetchCatalog(answer('{}'), 'c1')).toMatchObject({
      ok: false,
      problem: { code: 'studio.answer_invalid' },
    });
    expect(
      await fetchCatalog(answer('{"code":"studio.connection_busy"}', 503), 'c1'),
    ).toMatchObject({ ok: false, problem: { code: 'studio.connection_busy', status: 503 } });
    const offline = (async () => {
      throw new TypeError('offline');
    }) as unknown as typeof fetch;
    expect(await fetchCatalog(offline, 'c1')).toMatchObject({
      ok: false,
      problem: { code: 'studio.offline' },
    });
  });
});

describe('STU-022: parseDdl, parsePolicies and parseRowCount', () => {
  const measurement = PG_POLICIES.measurement as {
    policies: Record<string, unknown>[];
    current_role: Record<string, unknown>;
  };
  /** The measurement policies with the first policy changed. */
  const withPolicy = (change: Record<string, unknown>) => ({
    ...measurement,
    policies: [{ ...measurement.policies[0], ...change }, ...measurement.policies.slice(1)],
  });
  const withRole = (change: Record<string, unknown>) => {
    const role = { ...measurement.current_role, ...change };
    for (const key of Object.keys(change)) if (change[key] === undefined) delete role[key];
    return { ...measurement, current_role: role };
  };

  it('missing or null optional parts read as none, not as a refusal', () => {
    const {
      using: _using,
      with_check: _check,
      ...bare
    } = measurement.policies[0] as Record<string, unknown>;
    const policies = parsePolicies({ ...measurement, owner: null, policies: [bare] });
    expect(policies?.owner).toBeNull();
    expect(policies?.policies[0]).toMatchObject({ using: null, withCheck: null });
    expect(parsePolicies(withRole({ name: null }))?.currentRole.name).toBeNull();
    const { notes: _notes, owner: _owner, ...ddl } = PG_DDL.device as Record<string, unknown>;
    expect(parseDdl(ddl)).toMatchObject({ owner: null, notes: [] });
    expect(parseDdl({ ...ddl, notes: null })?.notes).toEqual([]);
  });

  it('read the backend shapes', () => {
    const ddl = parseDdl(PG_DDL.measurement_id_seq);
    expect(ddl?.statements).toHaveLength(2);
    expect(ddl?.ddl).toMatch(/^CREATE SEQUENCE public\.measurement_id_seq\n/);
    expect(ddl?.owner).toBe('measure_owner');
    expect(ddl?.notes).toHaveLength(1);
    const policies = parsePolicies(PG_POLICIES.measurement);
    expect(policies?.rlsEnabled).toBe(true);
    expect(policies?.rlsApplies).toBe(true);
    expect(policies?.currentRole).toEqual({
      name: 'analyst',
      superuser: false,
      bypassRls: false,
      owner: false,
    });
    expect(policies?.policies.map((policy) => [policy.command, policy.permissive])).toEqual([
      ['SELECT', true],
      ['ALL', false],
    ]);
    expect(policies?.policies[1]?.withCheck).toBe('false');
    expect(parseRowCount(SQLITE_COUNTS.part)).toEqual({
      schema: 'main',
      name: 'part',
      count: 1234,
      elapsedMs: 3,
    });
  });

  it.each([
    ['DDL without statements', () => parseDdl({ ...PG_DDL.device, statements: null })],
    ['DDL without its text', () => parseDdl({ ...PG_DDL.device, ddl: 1 })],
    [
      'DDL with a statement that is not text',
      () => parseDdl({ ...PG_DDL.device, statements: [{}] }),
    ],
    ['policies without the list', () => parsePolicies({ ...PG_POLICIES.device, policies: {} })],
    [
      'policies without rls_enabled',
      () => parsePolicies({ ...PG_POLICIES.device, rls_enabled: 1 }),
    ],
    [
      'policies without rls_applies',
      () => parsePolicies({ ...PG_POLICIES.device, rls_applies: null }),
    ],
    ['policies without the role', () => parsePolicies({ ...PG_POLICIES.device, current_role: [] })],
    [
      'a policy without a name',
      () =>
        parsePolicies({
          ...PG_POLICIES.device,
          policies: [{ permissive: true, command: 'ALL', roles: [] }],
        }),
    ],
    // L-1 of stu-022-page-review.md: a part that is present but not what it claims refuses the whole answer.
    ['a DDL owner that is not text', () => parseDdl({ ...PG_DDL.device, owner: 7 })],
    ['DDL notes that are not a list', () => parseDdl({ ...PG_DDL.device, notes: 'note' })],
    ['a DDL note that is not text', () => parseDdl({ ...PG_DDL.device, notes: ['ok', 1] })],
    ['a policies owner that is not text', () => parsePolicies({ ...PG_POLICIES.device, owner: 7 })],
    ['a USING that is not text', () => parsePolicies(withPolicy({ using: 42 }))],
    ['a WITH CHECK that is not text', () => parsePolicies(withPolicy({ with_check: { a: 1 } }))],
    // L-3 (M3): a list of roles with an entry that is not text.
    ['a role that is not text', () => parsePolicies(withPolicy({ roles: ['analyst', 1] }))],
    ...((['superuser', 'bypass_rls', 'owner'] as const).flatMap((key) => [
      [`a role flag ${key} that is not a boolean`, () => parsePolicies(withRole({ [key]: 'yes' }))],
      [`a missing role flag ${key}`, () => parsePolicies(withRole({ [key]: undefined }))],
    ]) as [string, () => unknown][]),
    ['a role name that is not text', () => parsePolicies(withRole({ name: 5 }))],
    ['a count that is not a count', () => parseRowCount({ ...SQLITE_COUNTS.part, count: -1 })],
    ['a count without a name', () => parseRowCount({ ...SQLITE_COUNTS.part, name: null })],
  ])('refuse %s', (_, parse) => {
    expect(parse()).toBeNull();
  });

  it('only GET in the query form, and an answer for another object or connection is refused', async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const answer = (body: unknown) =>
      (async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(input), init });
        return new Response(JSON.stringify(body), { status: 200 });
      }) as unknown as typeof fetch;
    const invalid = { ok: false, problem: { code: 'studio.answer_invalid' } };
    expect(await fetchDdl(answer(PG_DDL.device), 'c-pg', 'public', 'device')).toMatchObject({
      ok: true,
    });
    expect(await fetchDdl(answer(PG_DDL.device), 'c-pg', 'public', 'measurement')).toMatchObject(
      invalid,
    );
    expect(await fetchDdl(answer(PG_DDL.device), 'c-other', 'public', 'device')).toMatchObject(
      invalid,
    );
    expect(
      await fetchPolicies(answer(PG_POLICIES.device), 'c-pg', 'audit', 'device'),
    ).toMatchObject(invalid);
    expect(
      await fetchRowCount(answer(SQLITE_COUNTS.part), 'c-sqlite', 'main', 'material'),
    ).toMatchObject(invalid);
    const { connection_id: _, ...anonymous } = SQLITE_COUNTS.part as { connection_id: string };
    expect(await fetchRowCount(answer(anonymous), 'c-sqlite', 'main', 'part')).toMatchObject({
      ok: true,
    });
    await fetchPolicies(answer(PG_POLICIES.device), 'c 1', 'my/schema', 'a/b&c');
    expect(calls.map((call) => call.url)).toEqual([
      '/api/connections/c-pg/ddl?schema=public&name=device',
      '/api/connections/c-pg/ddl?schema=public&name=measurement',
      '/api/connections/c-other/ddl?schema=public&name=device',
      '/api/connections/c-pg/policies?schema=audit&name=device',
      '/api/connections/c-sqlite/row-count?schema=main&name=material',
      '/api/connections/c-sqlite/row-count?schema=main&name=part',
      '/api/connections/c%201/policies?schema=my%2Fschema&name=a%2Fb%26c',
    ]);
    for (const call of calls) {
      expect(call.init?.method).toBeUndefined();
      expect(call.init?.credentials).toBe('same-origin');
    }
  });
});
