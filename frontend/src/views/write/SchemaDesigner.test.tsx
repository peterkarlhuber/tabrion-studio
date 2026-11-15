/**
 * STU-CR-4: the Schema Designer's table view is hidden while its flag is off, unlocks first, starts a draft, sends one
 * edit operation at a time with the revision it shows (never a model or a plan), shows the server's refusals by field
 * and by tabrion's reason, and shows the read-only plan preview with its SQL and hash. axe finds no serious or
 * critical violation (jsdom: no colour contrast; the E2E test has it).
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';

import { parseDraft, parsePreview } from '../../api/schemaDesigner';
import { SchemaDesignerSection } from './SchemaDesigner';

const PROFILE = {
  id: 'user:parts',
  source: 'user',
  name: 'parts',
  engine: 'sqlite',
  environment: 'dev',
  path: '/data/parts.db',
};
const PROD = { ...PROFILE, id: 'user:live', name: 'live', environment: 'prod' };

function table(name: string, status = 'unchanged', extra: Record<string, unknown> = {}) {
  return {
    name,
    status,
    columns: [
      {
        name: 'id',
        type: 'INTEGER',
        nullable: true,
        default: null,
        identity: null,
        generated: null,
      },
      {
        name: 'material',
        type: 'TEXT',
        nullable: true,
        default: null,
        identity: null,
        generated: null,
      },
    ],
    primary_key: ['id'],
    unique: [],
    foreign_keys: [],
    checks: [],
    indexes: [],
    triggers: 0,
    policies: 0,
    features: [],
    ...extra,
  };
}

function draft(revision: number, tables: unknown[] = [table('parts')]) {
  return {
    connection_id: 'c_1',
    engine: 'sqlite',
    schema: 'main',
    revision,
    edits: revision,
    base_fingerprint: `v2:sha256:${'a'.repeat(64)}`,
    draft_fingerprint: `v2:sha256:${'b'.repeat(64)}`,
    changed: revision > 0,
    tables,
    dropped: [],
    limits: { tables: 500, columns: 250, elements: 100, edits: 500 },
  };
}

const PREVIEW = {
  connection_id: 'c_1',
  revision: 1,
  dialect: 'sqlite',
  server_version: [3, 45, 1],
  source_fingerprint: `v2:sha256:${'a'.repeat(64)}`,
  target_fingerprint: `v2:sha256:${'b'.repeat(64)}`,
  plan_hash: `sha256:${'c'.repeat(64)}`,
  risks: ['additive', 'data_changing'],
  transactional: true,
  base_changed: true,
  steps: [
    {
      index: 0,
      operation: 'add_column',
      target: 'main.parts',
      risks: ['additive', 'data_changing'],
      transactional: true,
      statements: [
        {
          sql: 'ALTER TABLE parts ADD COLUMN qty INTEGER DEFAULT (0)',
          target: 'main.parts.qty',
          transactional: true,
        },
      ],
    },
  ],
  executable: false,
};

interface Call {
  method: string;
  url: string;
  body: Record<string, unknown> | null;
}

interface Answers {
  areas?: Record<string, boolean>;
  unlocked?: boolean;
  started?: boolean;
  edit?: { status: number; body: unknown };
}

const ON = { data_editor: false, schema_designer: true, database_manager: false };

function setup(answers: Answers = {}) {
  const calls: Call[] = [];
  let unlocked = answers.unlocked ?? true;
  let current: ReturnType<typeof draft> | null = answers.started === false ? null : draft(0);
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
    calls.push({ method, url, body });
    const json = (value: unknown, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: {
          'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json',
        },
      });
    const problem = (code: string, issues: unknown[] = []) =>
      json({ code, kind: 'request', status: 400, title: 'x', detail: 'x', issues }, 400);
    if (url === '/api/write/areas') return json({ areas: answers.areas ?? ON });
    if (url === '/api/profiles')
      return json({
        profiles: [PROFILE, PROD],
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
    if (url === '/api/write/unlocks' && method === 'GET')
      return json({
        unlocks: unlocked
          ? [{ connection_id: 'c_1', write_profile_id: 'write:parts-w', environment: 'dev' }]
          : [],
      });
    if (url === '/api/write/unlocks' && method === 'POST') {
      unlocked = true;
      return json({ connection_id: 'c_1' });
    }
    if (url === '/api/write/profiles')
      return json({
        write_profiles: [
          {
            id: 'write:parts-w',
            name: 'parts-w',
            kind: 'write',
            engine: 'sqlite',
            read_profile_id: 'user:parts',
            secret: { confirmed: null, foreign: null },
          },
        ],
      });
    if (url === '/api/write/schema-designer/draft' && method === 'POST') {
      current = draft(0);
      return json(current, 201);
    }
    if (url === '/api/write/schema-designer/draft' && method === 'DELETE') {
      current = null;
      return new Response(null, { status: 204 });
    }
    if (url === '/api/write/schema-designer/draft/view')
      return current === null ? problem('studio.schema_draft_missing') : json(current);
    if (url === '/api/write/schema-designer/draft/edits') {
      if (answers.edit !== undefined) return json(answers.edit.body, answers.edit.status);
      if (current === null) return problem('studio.schema_draft_missing');
      const next = current.revision + 1;
      current = draft(next, [
        table('parts', 'changed', {
          columns: [
            ...table('parts').columns,
            {
              name: 'qty',
              type: 'INTEGER',
              nullable: true,
              default: '0',
              identity: null,
              generated: null,
            },
          ],
        }),
      ]);
      return json(current);
    }
    if (url === '/api/write/schema-designer/draft/preview') return json(PREVIEW);
    return new Response(null, { status: 404 });
  };
  const view = render(<SchemaDesignerSection fetcher={fetcher} />);
  return { calls, view };
}

async function serious(container: HTMLElement): Promise<string[]> {
  const result = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
  return result.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => v.id);
}

describe('the answers of the draft routes', () => {
  it('parses a draft and a preview defensively', () => {
    expect(parseDraft(draft(2))?.tables[0]?.primaryKey).toEqual(['id']);
    expect(parseDraft({ ...draft(0), engine: 'oracle' })).toBeNull();
    expect(parseDraft({ ...draft(0), tables: [{ name: 'x' }] })).toBeNull();
    expect(parsePreview(PREVIEW)?.steps[0]?.statements[0]?.target).toBe('main.parts.qty');
    expect(parsePreview({ ...PREVIEW, steps: [{ index: 0 }] })).toBeNull();
    expect(parsePreview('x')).toBeNull();
  });
});

describe('SchemaDesignerSection (STU-CR-4)', () => {
  it.each([
    { data_editor: false, schema_designer: false, database_manager: false },
    { data_editor: true, schema_designer: false, database_manager: true },
  ])('renders nothing while the Schema Designer flag is off (%o)', async (areas) => {
    const { calls } = setup({ areas });
    await waitFor(() => expect(calls.map((c) => c.url)).toContain('/api/write/areas'));
    expect(screen.queryByTestId('schema-designer')).toBeNull();
    expect(calls.some((c) => c.url.includes('schema-designer'))).toBe(false);
  });

  it('offers only connections that are not PROD and unlocks first', async () => {
    const user = userEvent.setup();
    const { calls } = setup({ unlocked: false, started: false });
    await screen.findByTestId('schema-designer');
    const select = await screen.findByLabelText('Connection');
    expect(
      within(select)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['parts']);
    await user.type(await screen.findByLabelText(/Type parts-w/), 'parts-w');
    await user.click(screen.getByRole('button', { name: 'Unlock' }));
    expect(await screen.findByTestId('schema-designer-start')).toBeInTheDocument();
    expect(calls.some((c) => c.url === '/api/write/unlocks' && c.method === 'POST')).toBe(true);
  });

  it('starts a draft, sends one edit with its revision, and never sends a model', async () => {
    const user = userEvent.setup();
    const { calls } = setup({ started: false });
    await user.click(await screen.findByRole('button', { name: 'Start draft' }));
    const card = await screen.findByTestId('schema-designer-table');
    expect(within(card).getByRole('heading', { name: 'parts' })).toBeInTheDocument();
    await user.click(within(card).getByRole('button', { name: 'Add column' }));
    const form = screen.getByTestId('schema-designer-form');
    await user.type(within(form).getByLabelText('Column name'), 'qty');
    await user.type(within(form).getByLabelText('Type'), 'INTEGER');
    await user.type(within(form).getByLabelText('Default'), '0');
    await user.click(within(form).getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(screen.queryByTestId('schema-designer-form')).toBeNull());
    const edit = calls.find((c) => c.url.endsWith('/draft/edits'));
    expect(edit?.body).toEqual({
      connection_id: 'c_1',
      revision: 0,
      edit: {
        op: 'add_column',
        table: 'parts',
        column: { name: 'qty', type: 'INTEGER', nullable: true, default: '0' },
      },
    });
    expect(screen.getByText('changed')).toBeInTheDocument();
    for (const call of calls) {
      for (const key of ['model', 'snapshot', 'plan', 'target', 'steps']) {
        expect(call.body ?? {}).not.toHaveProperty(key);
      }
    }
  });

  it('shows a refused edit by field and by tabrion reason', async () => {
    const user = userEvent.setup();
    setup({
      edit: {
        status: 400,
        body: {
          code: 'studio.schema_plan_refused',
          kind: 'request',
          status: 400,
          title: 'x',
          detail: 'x',
          issues: [
            { pointer: 'plan.reason', code: 'plan.unsupported_feature' },
            { pointer: 'plan.feature', code: 'plan.partitioned' },
          ],
        },
      },
    });
    const card = await screen.findByTestId('schema-designer-table');
    await user.click(within(card).getByRole('button', { name: 'Add column' }));
    const form = screen.getByTestId('schema-designer-form');
    await user.type(within(form).getByLabelText('Column name'), 'qty');
    await user.type(within(form).getByLabelText('Type'), 'INTEGER');
    await user.click(within(form).getByRole('button', { name: 'Apply' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Tabrion refuses to plan this change',
    );
    const issues = screen.getByTestId('schema-designer-plan-issues');
    expect(issues).toHaveTextContent(
      'Tabrion does not change a table with this feature: partitioned.',
    );
  });

  it('shows an edit issue at its field', async () => {
    const user = userEvent.setup();
    setup({
      edit: {
        status: 400,
        body: {
          code: 'studio.schema_edit_invalid',
          kind: 'request',
          status: 400,
          title: 'x',
          detail: 'x',
          issues: [{ pointer: 'edit.column_name', code: 'edit.column_exists' }],
        },
      },
    });
    const card = await screen.findByTestId('schema-designer-table');
    await user.click(within(card).getByRole('button', { name: 'Add column' }));
    const form = screen.getByTestId('schema-designer-form');
    await user.type(within(form).getByLabelText('Column name'), 'id');
    await user.type(within(form).getByLabelText('Type'), 'INTEGER');
    await user.click(within(form).getByRole('button', { name: 'Apply' }));
    expect(
      await within(form).findByText('A column with this name exists in the table.'),
    ).toBeInTheDocument();
    expect(within(form).getByLabelText('Column name')).toHaveAttribute('aria-invalid', 'true');
  });

  it('shows the read-only plan preview with its SQL and hash', async () => {
    const user = userEvent.setup();
    const { view } = setup();
    await user.click(await screen.findByRole('button', { name: 'Show plan' }));
    const preview = await screen.findByTestId('schema-designer-preview');
    expect(within(preview).getByRole('status')).toHaveTextContent(
      'Tabrion plans 1 step. This is a preview: nothing is run from here.',
    );
    expect(screen.getByTestId('schema-designer-plan-hash')).toHaveTextContent(PREVIEW.plan_hash);
    expect(screen.getByTestId('schema-designer-base-changed')).toBeInTheDocument();
    const step = screen.getByTestId('schema-designer-step');
    expect(step).toHaveTextContent('Add column main.parts');
    expect(step).toHaveTextContent('changes data');
    expect(step).toHaveTextContent('ALTER TABLE parts ADD COLUMN qty INTEGER DEFAULT (0)');
    // No button runs the plan.
    expect(screen.queryByRole('button', { name: /run|execute|ausführen/i })).toBeNull();
    expect(await serious(view.container)).toEqual([]);
  });

  it('discards the draft', async () => {
    const user = userEvent.setup();
    const { calls } = setup();
    await user.click(await screen.findByRole('button', { name: 'Discard draft' }));
    expect(await screen.findByTestId('schema-designer-start')).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/draft'))).toBe(true);
  });

  it('has no serious or critical axe violation with a form open', async () => {
    const user = userEvent.setup();
    const { view } = setup();
    const card = await screen.findByTestId('schema-designer-table');
    expect(await serious(view.container)).toEqual([]);
    await user.click(within(card).getByRole('button', { name: 'Add constraint' }));
    await user.selectOptions(screen.getByLabelText('Kind'), 'foreign_key');
    expect(await serious(view.container)).toEqual([]);
  });
});
