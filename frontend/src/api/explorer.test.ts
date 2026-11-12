/** STU-010: the explorer's parsers fail closed, and its requests only read. */
import { PG_EXTENSIONS, PG_TABLES, SQLITE_CATALOG, SQLITE_INFO } from '../harness/explorer';
import {
  count,
  epochMs,
  fetchCatalog,
  fetchTable,
  parseCatalog,
  parseExtensions,
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
