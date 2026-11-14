/**
 * STU-055: the vector data view. "Inspect" opens the profile's read-only connection (D-02 first for PROD) and reads
 * `GET .../vectors`; nothing on the page writes. A column is "ANN index" only when the server says so and lists a valid
 * ANN index, everything else "Exact search (no ANN index)"; sqlite-vec is "not determinable"; hints and counts are
 * worded in en/de; database names are escaped. The parser fails closed.
 */
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { parseVectors } from '../../api/vectors';
import i18n from '../../i18n';
import en from '../../i18n/locales/en.json';
import { VectorsView } from './VectorsView';

const BIDI = String.fromCodePoint(0x202e);

const PROFILES = {
  profiles: [
    {
      id: 'project:docs',
      source: 'project',
      name: 'docs',
      engine: 'postgresql',
      environment: 'dev',
      host: 'localhost',
      database: 'd',
      user: 'ro',
      sslmode: 'prefer',
      password: null,
      secret: { source: null, has_secret: null },
    },
    {
      id: 'user:prod',
      source: 'user',
      name: 'prod-db',
      engine: 'postgresql',
      environment: 'prod',
      host: 'db.internal',
      database: 'm',
      user: 'ro',
      sslmode: 'verify-full',
      password: { source: 'keyring', target: '' },
      secret: { source: 'keyring', has_secret: true },
    },
  ],
  project_state: 'trusted',
  issues: { user: [] },
  user_file: { digest: 'b'.repeat(64), hand_written: false },
};

const HNSW = {
  name: 'items_a',
  method: 'hnsw',
  state: 'valid',
  ann: true,
  operator_classes: ['vector_cosine_ops'],
  options: ['m=8'],
  size_bytes: 16384,
  definition: 'CREATE INDEX …',
};
const EXPRESSION = { ...HNSW, name: 'vectors_hnsw', expression: true, partial: true };

function column(extra: Record<string, unknown> = {}) {
  return {
    schema: 'public',
    table: 'items',
    column: 'a',
    type: 'vector(3)',
    kind: 'pgvector',
    layout: 'generic',
    dimensions: 3,
    search: 'ann',
    rows: { value: 20, kind: 'exact' },
    indexes: [HNSW],
    ...extra,
  };
}

const POSTGRES = {
  connection_id: 'c1',
  engine: 'postgresql',
  extension: {
    name: 'pgvector',
    state: 'installed',
    installed_version: '0.6.0',
    available_version: '0.7.0',
    schema: 'public',
    update_available: true,
    determinable: true,
  },
  columns: [
    column(),
    column({
      column: 'b',
      search: 'exact',
      rows: { value: 2_500_000, kind: 'estimate' },
      indexes: [{ ...HNSW, name: 'items_b', method: 'ivfflat', state: 'invalid' }],
    }),
    column({
      table: `orkexa${BIDI}_chunk_vectors`,
      column: 'embedding',
      type: 'real[]',
      kind: 'float_array',
      layout: 'orkexa',
      search: 'exact',
      rows: { value: 100_000, kind: 'more_than' },
      indexes: [EXPRESSION],
    }),
  ],
  identities: [
    {
      layout: 'orkexa',
      schema: 'public',
      table: 'orkexa_vector_spaces',
      tenant: 't1',
      namespace: 'default',
      space: 'default:k',
      state: 'active',
      provider: 'ollama',
      model: 'nomic-embed-text',
      digest: 'sha256:ab',
      normalized: true,
      dimensions: 3,
      complete: true,
      embeddings: { value: null, kind: 'timeout' },
    },
  ],
  hints: [
    {
      code: 'index_invalid',
      severity: 'warning',
      params: { schema: 'public', table: 'items', index: 'items_b' },
    },
    {
      code: 'exact_search',
      severity: 'info',
      params: { schema: 'public', table: 'items', column: 'b' },
    },
    {
      code: 'pgvector_update',
      severity: 'info',
      params: { installed: '0.6.0', available: '0.7.0' },
    },
    { code: 'counts_skipped', severity: 'info', params: { count: 2 } },
    { code: 'brand_new', severity: 'info', params: {} },
  ],
  limits: { exact_count_max: 100_000, max_columns: 200, max_counts: 50 },
};

const SQLITE_EMPTY = {
  connection_id: 'c1',
  engine: 'sqlite',
  extension: { name: 'sqlite-vec', state: 'not_determinable', determinable: false },
  columns: [],
  identities: [],
  hints: [
    { code: 'sqlite_vec_undeterminable', severity: 'info', params: {} },
    { code: 'no_vector_data', severity: 'info', params: {} },
  ],
  limits: { exact_count_max: 100_000 },
};

type Call = { url: string; method: string; body: unknown };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function server(report: unknown = POSTGRES) {
  const calls: Call[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = {
      url: String(input),
      method: init?.method ?? 'GET',
      body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
    };
    calls.push(call);
    if (call.url === '/api/profiles') return json(PROFILES);
    if (call.url === '/api/connections' && call.method === 'POST') {
      return json({ connection_id: 'c1' }, 201);
    }
    if (call.url === '/api/connections/c1/vectors') return json(report);
    return new Response(null, { status: 204 });
  });
  return { fetcher: fetcher as unknown as typeof fetch, calls };
}

async function inspect(report: unknown = POSTGRES) {
  const user = userEvent.setup();
  const { fetcher, calls } = server(report);
  render(<VectorsView fetcher={fetcher} />);
  await screen.findByRole('option', { name: 'docs (DEV)' });
  await user.click(screen.getByRole('button', { name: en.vectors.inspect }));
  return { user, calls };
}

afterEach(async () => {
  await act(async () => {
    await i18n.changeLanguage('en');
  });
});

describe('vector data view', () => {
  it('opens the read-only connection, reads the route and never writes anything else', async () => {
    const { user, calls } = await inspect();
    await screen.findByTestId('vectors-report');
    expect(
      calls.filter((call) => call.method !== 'GET').map((call) => [call.url, call.body]),
    ).toEqual([['/api/connections', { profile_id: 'project:docs', confirmed_environment: 'dev' }]]);
    expect(
      calls.some((call) => call.method === 'GET' && call.url === '/api/connections/c1/vectors'),
    ).toBe(true);
    // "Inspect again" reads on the shown connection and opens none.
    await user.click(screen.getByRole('button', { name: en.vectors.again }));
    await screen.findByTestId('vectors-report');
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1);
  });

  it('labels ANN and exact search from the catalog, with counts, indexes and identities', async () => {
    await inspect();
    const rows = await screen.findAllByTestId('vectors-column');
    const search = rows.map((row) => within(row).getByTestId('vectors-search'));
    expect(search.map((item) => item.dataset.search)).toEqual(['ann', 'exact', 'exact']);
    expect(search[0]).toHaveTextContent('ANN index (hnsw)');
    expect(search[1]).toHaveTextContent('Exact search (no ANN index)');
    expect(rows[0]).toHaveTextContent('vector_cosine_ops');
    expect(rows[0]).toHaveTextContent('m=8');
    expect(within(rows[1] as HTMLElement).getByTestId('vectors-index')).toHaveAttribute(
      'data-state',
      'invalid',
    );
    expect(rows[1]).toHaveTextContent('invalid, not used');
    expect(within(rows[1] as HTMLElement).getByTestId('vectors-rows')).toHaveTextContent(
      'about 2,500,000 rows (estimate)',
    );
    expect(within(rows[2] as HTMLElement).getByTestId('vectors-rows')).toHaveTextContent(
      'more than 100,000',
    );
    // A database name is escaped: the bidi override is shown, never applied.
    expect(rows[2]?.textContent).toContain('u202E_chunk_vectors');
    expect(rows[2]?.textContent).not.toContain(BIDI);
    expect(rows[2]).toHaveTextContent('Orkexa layout');
    expect(rows[2]).toHaveTextContent(
      'on an expression: a query must use the same expression to use it',
    );
    expect(rows[2]).toHaveTextContent('partial: covers only some rows');
    expect(screen.getByTestId('vectors-summary')).toHaveTextContent(
      '3 vector columns: 1 with an ANN index, 2 with exact search',
    );
    const identity = screen.getByTestId('vectors-identity');
    expect(identity).toHaveTextContent('nomic-embed-text from ollama');
    expect(identity).toHaveTextContent('Build sha256:ab');
    expect(identity).toHaveTextContent('unknown (the count took too long)');
    expect(screen.getByTestId('vectors-extension')).toHaveTextContent(
      'pgvector is installed, version 0.6.0, in schema public. Version 0.7.0 is available as an update.',
    );
    const hints = screen.getAllByTestId('vectors-hint');
    // exact_search is said by the table; the rest is listed, an unknown code with the generic text.
    expect(hints.map((hint) => hint.dataset.code)).toEqual([
      'index_invalid',
      'pgvector_update',
      'counts_skipped',
      'brand_new',
    ]);
    expect(hints[0]).toHaveTextContent('Warning: The index items_b on public.items is not valid');
    expect(hints[2]).toHaveTextContent('2 counts were skipped');
    expect(hints[3]).toHaveTextContent('(brand_new)');
  });

  it('never shows ANN when the server claims it without a valid ANN index', () => {
    const report = parseVectors({
      ...POSTGRES,
      columns: [
        column({ search: 'ann', indexes: [] }),
        column({ search: 'ann', indexes: [{ ...HNSW, state: 'invalid' }] }),
        column({ search: 'ann', indexes: [{ ...HNSW, ann: false, method: 'btree' }] }),
      ],
    });
    expect(report?.columns.map((item) => item.search)).toEqual(['exact', 'exact', 'exact']);
    expect(parseVectors({ ...POSTGRES, columns: null })).toBeNull();
    expect(parseVectors({ ...POSTGRES, extension: { state: 'odd' } })?.extension.state).toBe(
      'unknown',
    );
  });

  it('shows the empty state and says sqlite-vec is not determinable, in German too', async () => {
    await act(async () => {
      await i18n.changeLanguage('de');
    });
    const user = userEvent.setup();
    const { fetcher } = server(SQLITE_EMPTY);
    render(<VectorsView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'docs (DEV)' });
    await user.click(screen.getByRole('button', { name: 'Prüfen' }));
    await screen.findByText('Keine Vektordaten gefunden');
    expect(screen.getByTestId('vectors-extension')).toHaveAttribute(
      'data-state',
      'not_determinable',
    );
    expect(screen.getByTestId('vectors-extension')).toHaveTextContent('nicht feststellbar');
    expect(screen.queryByTestId('vectors-columns')).toBeNull();
    expect(screen.getAllByTestId('vectors-hint').map((hint) => hint.dataset.code)).toEqual([
      'sqlite_vec_undeterminable',
      'no_vector_data',
    ]);
  });

  it('asks D-02 before it opens a PROD connection', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server();
    render(<VectorsView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'prod-db (PROD)' });
    await user.selectOptions(screen.getByLabelText(en.vectors.profile), 'user:prod');
    await user.click(screen.getByRole('button', { name: en.vectors.inspect }));
    await screen.findByRole('dialog');
    expect(calls.some((call) => call.method === 'POST')).toBe(false);
  });

  it('shows a refusal of the route as the page text, not as data', async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(`${init?.method ?? 'GET'} ${url}`);
      if (url === '/api/profiles') return json(PROFILES);
      if (url === '/api/connections') return json({ connection_id: 'c1' }, 201);
      return json({ code: 'studio.connection_not_found', kind: 'config', status: 404 }, 404);
    }) as typeof fetch;
    render(<VectorsView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'docs (DEV)' });
    await user.click(screen.getByRole('button', { name: en.vectors.inspect }));
    await screen.findByTestId('vectors-problem');
    expect(screen.queryByTestId('vectors-report')).toBeNull();
  });
});
