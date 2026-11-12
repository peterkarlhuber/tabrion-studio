/** STU-013 page: the viewer's requests and its fail-closed parsers. */
import {
  fetchCell,
  fetchPage,
  FILTER_TOO_LONG,
  MAX_QUERY_BYTES,
  pageQuery,
  parseCell,
  parsePage,
  queryFits,
  type ViewerRequest,
} from './viewer';

const ASKED = { schema: 'main', name: 'part', limit: 100, sort: null, desc: false };

const PAGE = {
  connection_id: 'c1',
  schema: 'main',
  name: 'part',
  columns: [
    { name: 'id', type: 'INTEGER' },
    { name: 'note', type: 'TEXT' },
  ],
  rows: [
    [1, 'a'],
    [2, null],
  ],
  truncated_cells: [[0, 1]],
  truncated: null,
  page: 'tok1',
  next: 'tok1',
  paging: 'primary_key',
  hint: null,
  sort: null,
  desc: false,
  limit: 100,
};

describe('parsePage', () => {
  it('reads a page', () => {
    expect(parsePage(PAGE, ASKED)).toEqual({
      columns: [
        { name: 'id', type: 'INTEGER' },
        { name: 'note', type: 'TEXT' },
      ],
      rows: PAGE.rows,
      truncatedCells: [[0, 1]],
      cutByBytes: false,
      page: 'tok1',
      next: 'tok1',
      paging: 'primary_key',
      hint: null,
    });
    const last = parsePage(
      { ...PAGE, next: null, truncated: 'bytes', paging: 'rowid', hint: 'no_primary_key' },
      ASKED,
    );
    expect(last).toMatchObject({
      next: null,
      cutByBytes: true,
      paging: 'rowid',
      hint: 'no_primary_key',
    });
    // An unknown hint is not shown; a column without a type has an empty one.
    expect(parsePage({ ...PAGE, hint: 'x' }, ASKED)?.hint).toBeNull();
    expect(
      parsePage({ ...PAGE, columns: [{ name: 'id' }, { name: 'n' }] }, ASKED)?.columns[0],
    ).toEqual({
      name: 'id',
      type: '',
    });
  });

  it.each([
    ['no object', null],
    ['a list', []],
    ['another object', { ...PAGE, name: 'material' }],
    ['another schema', { ...PAGE, schema: 'x' }],
    ['another limit', { ...PAGE, limit: 50 }],
    ['another sort', { ...PAGE, sort: 'id' }],
    ['another direction', { ...PAGE, desc: true }],
    ['no columns', { ...PAGE, columns: undefined }],
    ['a column without a name', { ...PAGE, columns: [{ type: 'x' }, { name: 'n' }] }],
    ['no rows', { ...PAGE, rows: undefined }],
    ['a row with too few values', { ...PAGE, rows: [[1]] }],
    ['a row that is not a list', { ...PAGE, rows: [{ id: 1 }] }],
    ['more rows than asked for', { ...PAGE, rows: Array.from({ length: 101 }, () => [1, 'a']) }],
    ['a cut cell outside the page', { ...PAGE, truncated_cells: [[2, 0]] }],
    ['a cut cell outside the columns', { ...PAGE, truncated_cells: [[0, 2]] }],
    ['a cut cell that is not a pair', { ...PAGE, truncated_cells: ['0,1'] }],
    ['no page token', { ...PAGE, page: '' }],
    ['a next token that is not text', { ...PAGE, next: 5 }],
    ['an unknown paging', { ...PAGE, paging: 'magic' }],
  ])('refuses %s', (_, body) => {
    expect(parsePage(body, ASKED)).toBeNull();
  });

  // The server always echoes what it answered for; an answer that leaves it out is not the one asked for (I-2).
  it.each(['schema', 'name', 'limit', 'sort', 'desc'])('refuses a page without %s', (field) => {
    const body = Object.fromEntries(Object.entries(PAGE).filter(([key]) => key !== field));
    expect(parsePage(body, ASKED)).toBeNull();
  });
});

describe('parseCell', () => {
  it('reads a value with its size', () => {
    expect(parseCell({ value: 'x', size: 1, truncated: false })).toEqual({
      value: 'x',
      size: 1,
      truncated: false,
    });
    expect(parseCell({ value: null, size: 0, truncated: false })?.value).toBeNull();
  });

  it.each([
    ['no value', { size: 1, truncated: false }],
    ['a negative size', { value: 'x', size: -1, truncated: false }],
    ['a size that is not a count', { value: 'x', size: 1.5, truncated: false }],
    ['no truncated flag', { value: 'x', size: 1 }],
    ['a list', ['x']],
  ])('refuses %s', (_, body) => {
    expect(parseCell(body)).toBeNull();
  });
});

describe('fetchPage and fetchCell', () => {
  function recording(body: unknown, status = 200) {
    const urls: string[] = [];
    const fetcher = (async (input: RequestInfo | URL) => {
      urls.push(String(input));
      return new Response(JSON.stringify(body), { status });
    }) as typeof fetch;
    return { fetcher, urls };
  }

  it('asks with the query form, the sort, the filters as JSON and the token', async () => {
    const { fetcher, urls } = recording({ ...PAGE, sort: 'id', desc: true, limit: 50 });
    const result = await fetchPage(fetcher, 'c1', 'main', 'part', {
      limit: 50,
      sort: 'id',
      desc: true,
      filters: [
        { column: 'note', op: 'contains', value: '50%_' },
        { column: 'id', op: 'is_null', value: 'ignored' },
      ],
      after: 'tok0',
    });
    expect(result.ok).toBe(true);
    const url = new URL(urls[0] ?? '', 'http://x');
    expect(url.pathname).toBe('/api/connections/c1/rows');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      schema: 'main',
      name: 'part',
      limit: '50',
      sort: 'id',
      desc: 'true',
      filter: JSON.stringify([
        { column: 'note', op: 'contains', value: '50%_' },
        { column: 'id', op: 'is_null' },
      ]),
      after: 'tok0',
    });
  });

  it('a name with "/" stays in the query; the page of another connection is refused', async () => {
    const { fetcher, urls } = recording({ ...PAGE, connection_id: 'c2', name: 'a/b' });
    const result = await fetchPage(fetcher, 'c1', 'main', 'a/b', {
      limit: 100,
      sort: null,
      desc: false,
      filters: [],
      after: null,
    });
    expect(urls[0]).toBe('/api/connections/c1/rows?schema=main&name=a%2Fb&limit=100');
    expect(result).toEqual({
      ok: false,
      problem: expect.objectContaining({ code: 'studio.answer_invalid' }),
    });
  });

  it('a page without its connection is refused', async () => {
    const body: Record<string, unknown> = { ...PAGE };
    delete body.connection_id;
    const { fetcher } = recording(body);
    const result = await fetchPage(fetcher, 'c1', 'main', 'part', {
      limit: 100,
      sort: null,
      desc: false,
      filters: [],
      after: null,
    });
    expect(result).toEqual({
      ok: false,
      problem: expect.objectContaining({ code: 'studio.answer_invalid' }),
    });
  });

  it('passes the server problem on, and asks for one cell', async () => {
    const refused = recording(
      { code: 'studio.viewer_sort_unsupported', kind: 'request', status: 400 },
      400,
    );
    const result = await fetchPage(refused.fetcher, 'c1', 'public', 'doc', {
      limit: 100,
      sort: 'body',
      desc: false,
      filters: [],
      after: null,
    });
    expect(result).toEqual({
      ok: false,
      problem: expect.objectContaining({ code: 'studio.viewer_sort_unsupported' }),
    });
    const { fetcher, urls } = recording({ value: 'x', size: 1, truncated: false });
    expect(await fetchCell(fetcher, 'c1', 'main', 'part', 'tok1', 3, 'note')).toEqual({
      ok: true,
      value: { value: 'x', size: 1, truncated: false },
    });
    expect(urls[0]).toBe(
      '/api/connections/c1/cell?schema=main&name=part&page=tok1&row=3&column=note',
    );
  });
});

// M-1 of stu-013-page-review.md: the server refuses a request head over 16 KB with a plain 400, so a rows request
// whose query string is longer than 12 KB is refused before it is sent.
describe('the query string bound', () => {
  const request = (values: string[]): ViewerRequest => ({
    limit: 1000,
    sort: 'note',
    desc: true,
    filters: values.map((value) => ({ column: 'note', op: 'contains', value })),
    after: null,
  });

  it('counts the encoded bytes: three bytes of UTF-8 take nine', () => {
    const ascii = pageQuery('main', 'part', request(['a'.repeat(1000)])).length;
    const euro = pageQuery('main', 'part', request(['€'.repeat(1000)])).length;
    expect(euro - ascii).toBe(8 * 1000);
  });

  it.each([
    [
      'four ASCII values of 4,096 characters',
      ['a'.repeat(4096), 'b'.repeat(4096), 'c'.repeat(4096), 'd'.repeat(4096)],
    ],
    ['one value of 1,400 three-byte characters', ['€'.repeat(1400)]],
    ['one value of 1,100 four-byte characters', ['😀'.repeat(1100)]],
  ])('refuses %s without a request', async (_, values) => {
    expect(queryFits('main', 'part', request(values))).toBe(false);
    const urls: string[] = [];
    const fetcher = (async (input: RequestInfo | URL) => {
      urls.push(String(input));
      return new Response(JSON.stringify(PAGE));
    }) as typeof fetch;
    const result = await fetchPage(fetcher, 'c1', 'main', 'part', request(values));
    expect(result).toEqual({
      ok: false,
      problem: expect.objectContaining({ code: FILTER_TOO_LONG }),
    });
    expect(urls).toEqual([]);
  });

  it('sends a query string up to the bound', async () => {
    // Fill one ASCII value up to the bound exactly.
    const empty = pageQuery('main', 'part', request([''])).length;
    const fitting = request(['a'.repeat(MAX_QUERY_BYTES - empty)]);
    expect(pageQuery('main', 'part', fitting).length).toBe(MAX_QUERY_BYTES);
    expect(queryFits('main', 'part', fitting)).toBe(true);
    expect(queryFits('main', 'part', request(['a'.repeat(MAX_QUERY_BYTES - empty + 1)]))).toBe(
      false,
    );
    const urls: string[] = [];
    const fetcher = (async (input: RequestInfo | URL) => {
      urls.push(String(input));
      return new Response(JSON.stringify({ ...PAGE, sort: 'note', desc: true, limit: 1000 }));
    }) as typeof fetch;
    expect((await fetchPage(fetcher, 'c1', 'main', 'part', fitting)).ok).toBe(true);
    expect(urls).toHaveLength(1);
  });
});
