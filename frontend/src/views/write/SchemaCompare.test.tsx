/**
 * STU-XSD-2: the schema comparison is hidden while the Schema Designer flag is off; it offers every open connection
 * (PROD included: it is only read), sends the two sides and the plan wish (a snapshot file as its text, never a model
 * or a plan), shows the verdict, the differences as a table, the feature markers, a refused plan preview with its
 * reason, and a plan preview with its SQL but no plan hash and no run. axe finds no serious or critical violation.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';

import { MAX_SNAPSHOT_BYTES, parseComparison } from '../../api/schemaCompare';
import { SchemaCompareSection } from './SchemaCompare';

const PROFILES = [
  { id: 'user:parts', source: 'user', name: 'parts', engine: 'sqlite', environment: 'dev' },
  { id: 'user:live', source: 'user', name: 'live', engine: 'sqlite', environment: 'prod' },
];

function side(extra: Record<string, unknown> = {}) {
  return {
    source: 'connection',
    engine: 'sqlite',
    schema: 'main',
    environment: 'dev',
    fingerprint: `v2:sha256:${'a'.repeat(64)}`,
    tables: 2,
    views: 0,
    triggers: 0,
    policies: 0,
    row_security: 0,
    features: [],
    flagged: [],
    ...extra,
  };
}

const DIFFERENT = {
  expected: side(),
  actual: side({ environment: 'prod', fingerprint: `v2:sha256:${'b'.repeat(64)}` }),
  verdict: 'different',
  same_fingerprint: false,
  fingerprint_only: false,
  flagged: true,
  unknown_features: false,
  differences: [
    { kind: 'table_missing', table: 'main.orders', column: null, name: null, breaking: true },
    { kind: 'column_added', table: 'main.parts', column: 'qty', name: null, breaking: false },
  ],
  breaking: 1,
  plan: {
    status: 'refused',
    problem: {
      code: 'studio.schema_compare_plan_not_allowed',
      issues: [{ pointer: 'compare.plan', code: 'compare.plan_prod' }],
    },
  },
};

const READY = {
  ...DIFFERENT,
  actual: side({
    environment: 'test',
    flagged: [
      {
        table: 'main.kv',
        features: [{ kind: 'domain', name: 'qty', known: true, marker: 'domain:qty' }],
      },
    ],
  }),
  plan: {
    status: 'ready',
    dialect: 'sqlite',
    server_version: [3, 45, 1],
    source_fingerprint: `v2:sha256:${'b'.repeat(64)}`,
    target_fingerprint: `v2:sha256:${'a'.repeat(64)}`,
    risks: ['additive'],
    transactional: true,
    steps: [
      {
        index: 0,
        operation: 'create_table',
        target: 'main.orders',
        risks: ['additive'],
        transactional: true,
        statements: [
          { sql: 'CREATE TABLE orders (id INTEGER)', target: 'main.orders', transactional: true },
        ],
      },
    ],
    warnings: [{ code: 'not_modelled', table: null }],
  },
};

interface Call {
  url: string;
  body: Record<string, unknown> | null;
}

function setup(
  answers: { areas?: Record<string, boolean>; compare?: { status: number; body: unknown } } = {},
) {
  const calls: Call[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
    calls.push({ url, body });
    const json = (value: unknown, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: {
          'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json',
        },
      });
    if (url === '/api/write/areas')
      return json({
        areas: answers.areas ?? {
          data_editor: false,
          schema_designer: true,
          database_manager: false,
        },
      });
    if (url === '/api/profiles')
      return json({
        profiles: PROFILES,
        project_state: 'none',
        user_file: { digest: 'd'.repeat(64), hand_written: false },
        issues: {},
      });
    if (url === '/api/connections')
      return json({
        connections: [
          { connection_id: 'c_1', profile_id: 'user:parts', environment: 'dev' },
          { connection_id: 'c_prod', profile_id: 'user:live', environment: 'prod' },
        ],
      });
    if (url === '/api/write/schema-designer/compare') {
      const answer = answers.compare ?? { status: 200, body: DIFFERENT };
      return json(answer.body, answer.status);
    }
    return new Response(null, { status: 404 });
  };
  const view = render(<SchemaCompareSection fetcher={fetcher} />);
  return { calls, view };
}

async function serious(container: HTMLElement): Promise<string[]> {
  const result = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
  return result.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => v.id);
}

describe('the comparison answer', () => {
  it('is parsed defensively', () => {
    expect(parseComparison(DIFFERENT)?.differences[1]?.column).toBe('qty');
    expect(parseComparison(READY)?.plan).toMatchObject({
      status: 'ready',
      serverVersion: [3, 45, 1],
    });
    expect(parseComparison({ ...DIFFERENT, verdict: 'equal' })).toBeNull();
    expect(parseComparison({ ...DIFFERENT, differences: [{ kind: 1 }] })).toBeNull();
    expect(parseComparison({ ...DIFFERENT, expected: side({ tables: -1 }) })).toBeNull();
    expect(parseComparison({ ...DIFFERENT, plan: { status: 'ready' } })).toBeNull();
    expect(parseComparison({ ...DIFFERENT, plan: null })?.plan).toBeNull();
    expect(parseComparison('x')).toBeNull();
  });
});

describe('SchemaCompareSection (STU-XSD-2)', () => {
  it.each([
    { data_editor: false, schema_designer: false, database_manager: false },
    { data_editor: true, schema_designer: false, database_manager: true },
  ])('renders nothing while the Schema Designer flag is off (%o)', async (areas) => {
    const { calls } = setup({ areas });
    await waitFor(() => expect(calls.map((c) => c.url)).toContain('/api/write/areas'));
    expect(screen.queryByTestId('schema-compare')).toBeNull();
    expect(calls.some((c) => c.url.includes('schema-designer'))).toBe(false);
  });

  it('compares two connections, PROD included, and shows the differences and the refused plan', async () => {
    const user = userEvent.setup();
    const { calls, view } = setup();
    const actual = await screen.findByTestId('schema-compare-actual');
    const select = within(actual).getByTestId('schema-compare-actual-connection');
    expect(
      within(select)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['parts (DEV)', 'live (PROD)']);
    await user.selectOptions(select, 'c_prod');
    await user.click(screen.getByTestId('schema-compare-plan'));
    await user.click(screen.getByTestId('schema-compare-submit'));
    const result = await screen.findByTestId('schema-compare-result');
    expect(result.dataset.verdict).toBe('different');
    expect(screen.getByTestId('schema-compare-verdict').textContent).toContain(
      '2 differences, 1 of them breaking',
    );
    const rows = screen.getAllByTestId('schema-compare-difference');
    expect(rows.map((row) => row.dataset.kind)).toEqual(['table_missing', 'column_added']);
    expect(rows[0]?.textContent).toContain('main.orders');
    expect(screen.getByTestId('schema-compare-plan-refused').textContent).toContain(
      'No plan is made for a PROD database.',
    );
    const sent = calls.find((c) => c.url === '/api/write/schema-designer/compare');
    expect(sent?.body).toEqual({
      expected: { source: 'connection', connection_id: 'c_1' },
      actual: { source: 'connection', connection_id: 'c_prod' },
      plan: true,
    });
    expect(await serious(view.container)).toEqual([]);
  });

  it('sends a snapshot file as its text only and refuses two files on the page', async () => {
    const user = userEvent.setup();
    const { calls } = setup();
    await screen.findByTestId('schema-compare-expected');
    await user.click(screen.getByTestId('schema-compare-expected-source-file'));
    const text = '{"format":2,"dialect":"sqlite","level":2,"tables":[]}';
    await user.upload(
      screen.getByTestId('schema-compare-expected-file-input'),
      new File([text], 'snap.json', { type: 'application/json' }),
    );
    await screen.findByTestId('schema-compare-expected-file-name');
    await user.click(screen.getByTestId('schema-compare-submit'));
    await screen.findByTestId('schema-compare-result');
    const sent = calls.find((c) => c.url === '/api/write/schema-designer/compare');
    expect(sent?.body?.expected).toEqual({ source: 'file', text });
    expect(sent?.body?.plan).toBe(false);
    await user.click(screen.getByTestId('schema-compare-actual-source-file'));
    expect(screen.getByTestId('schema-compare-two-files')).toBeTruthy();
    expect((screen.getByTestId('schema-compare-submit') as HTMLButtonElement).disabled).toBe(true);
  });

  it('refuses an oversized file before reading it', async () => {
    const user = userEvent.setup();
    const { calls } = setup();
    await screen.findByTestId('schema-compare-expected');
    await user.click(screen.getByTestId('schema-compare-expected-source-file'));
    const big = new File(['x'], 'big.json', { type: 'application/json' });
    Object.defineProperty(big, 'size', { value: MAX_SNAPSHOT_BYTES + 1 });
    await user.upload(screen.getByTestId('schema-compare-expected-file-input'), big);
    expect(await screen.findByText('The file is larger than 2 MiB.')).toBeTruthy();
    expect((screen.getByTestId('schema-compare-submit') as HTMLButtonElement).disabled).toBe(true);
    expect(calls.some((c) => c.url === '/api/write/schema-designer/compare')).toBe(false);
  });

  it('shows a plan preview with its SQL and the feature markers, and never offers a run', async () => {
    const user = userEvent.setup();
    setup({ compare: { status: 200, body: READY } });
    await user.click(await screen.findByTestId('schema-compare-submit'));
    const plan = await screen.findByTestId('schema-compare-plan-result');
    expect(plan.textContent).toContain('CREATE TABLE orders (id INTEGER)');
    expect(plan.textContent).toContain('never run from here');
    expect(within(plan).queryByRole('button')).toBeNull();
    const features = screen
      .getAllByTestId('schema-compare-feature')
      .map((item) => item.textContent);
    expect(features).toEqual(['Compared (actual), main.kv: domain column qty']);
    expect(screen.getByTestId('schema-compare-flagged')).toBeTruthy();
  });

  it('shows a refused comparison with its catalog text and the side it is about', async () => {
    const user = userEvent.setup();
    setup({
      compare: {
        status: 400,
        body: {
          code: 'studio.schema_snapshot_invalid',
          kind: 'request',
          status: 400,
          title: 'x',
          detail: 'x',
          issues: [{ pointer: 'compare.expected', code: 'compare.format' }],
        },
      },
    });
    await user.click(await screen.findByTestId('schema-compare-submit'));
    expect((await screen.findByTestId('schema-compare-problem')).textContent).toContain(
      'not a level-2 schema snapshot',
    );
    expect(
      within(screen.getByTestId('schema-compare-expected')).getByText(/not a schema snapshot/),
    ).toBeTruthy();
    expect(screen.queryByTestId('schema-compare-result')).toBeNull();
  });
});
