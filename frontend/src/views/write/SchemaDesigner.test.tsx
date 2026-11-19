/**
 * STU-CR-4: the Schema Designer's table view is hidden while its flag is off, unlocks first, starts a draft, sends one
 * edit operation at a time with the revision it shows (never a model or a plan), shows the server's refusals by field
 * and by tabrion's reason, and shows the plan preview with its SQL and hash. axe finds no serious or critical
 * violation (jsdom: no colour contrast; the E2E test has it).
 *
 * STU-CR-5: the preview's warnings and why a plan is not run; the confirmation sends exactly what the user typed and
 * ticked with the recorded hash (never a plan); Run and Dry run only after it, neither focused by default; the
 * outcome with the backup's path; a refused run drops the preview; a table in a refusal is named from its position.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';

import { parseConfirmed, parseDraft, parseOutcome, parsePreview } from '../../api/schemaDesigner';
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
  environment: 'dev',
  write_profile_id: 'write:parts-w',
  executable: true,
  blocked: [],
  warnings: [
    { code: 'not_modelled', table: null },
    { code: 'sqlite_rowid', table: 'parts' },
    { code: 'fingerprint_gaps', table: null },
  ],
  confirmation: {
    profile: 'parts-w',
    tables: ['parts'],
    data_changing_steps: [0],
    locking: false,
    lock_timeout_s: 5,
    backup: { required: true, engine: 'sqlite', opt_out_allowed: true, opt_out_text: 'parts.db' },
  },
  expires_in_s: 300,
  revision_output: {
    available: false,
    blocked: ['no_alembic_project'],
    checks_source: false,
    max_message: 200,
  },
};
const BLOCKED = {
  ...PREVIEW,
  executable: false,
  blocked: ['migration_managed', 'alembic_project'],
  revision_output: { available: true, blocked: [], checks_source: false, max_message: 200 },
};
const WRITTEN = {
  plan_hash: PREVIEW.plan_hash,
  result: 'written',
  revision: '0123456789ab',
  down_revision: '0001',
  path: 'migrations/versions/0123456789ab_add_qty.py',
  source_fingerprint: PREVIEW.source_fingerprint,
  checks_source: false,
  steps: 1,
  code_trust_changed: true,
  audit_pending: false,
  draft_revision: 1,
};
const OUTCOME = {
  plan_hash: PREVIEW.plan_hash,
  result: 'committed',
  dry_run: false,
  steps_run: 1,
  fingerprint_after: `v2:sha256:${'d'.repeat(64)}`,
  backup: 'made',
  backup_path:
    '/home/u/.config/tabrion-studio/backups/schema-designer/20261115T101010Z-0a1b2c3d.sqlite',
  audit_pending: false,
  revision: 2,
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
  preview?: { status: number; body: unknown };
  run?: { status: number; body: unknown };
  revision?: { status: number; body: unknown };
  /** Holds the run's answer until it resolves (the page's state while a run and its backup are under way). */
  runGate?: Promise<void>;
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
    if (url === '/api/write/schema-designer/draft/preview')
      return answers.preview === undefined
        ? json(PREVIEW)
        : json(answers.preview.body, answers.preview.status);
    if (url === '/api/write/schema-designer/plan/confirm')
      return json({
        plan_hash: PREVIEW.plan_hash,
        confirmed: true,
        backup: 'make',
        expires_in_s: 280,
      });
    if (url === '/api/write/schema-designer/revision')
      return answers.revision === undefined
        ? json(WRITTEN, 201)
        : json(answers.revision.body, answers.revision.status);
    if (url === '/api/write/schema-designer/plan/run') {
      if (answers.runGate !== undefined) await answers.runGate;
      if (answers.run !== undefined) return json(answers.run.body, answers.run.status);
      return body?.dry_run === true
        ? json({
            ...OUTCOME,
            result: 'rolled_back',
            dry_run: true,
            backup: 'dry_run',
            backup_path: null,
          })
        : json(OUTCOME);
    }
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
    const parsed = parsePreview(PREVIEW);
    expect(parsed?.confirmation.backup.optOutText).toBe('parts.db');
    expect(parsed?.warnings[1]).toEqual({
      code: 'sqlite_rowid',
      table: 'parts',
      lockTimeoutS: null,
    });
    expect(parsePreview({ ...PREVIEW, blocked: [1] })).toBeNull();
    expect(parsePreview({ ...PREVIEW, confirmation: { profile: 'x' } })).toBeNull();
    expect(
      parseConfirmed({ plan_hash: 'h', confirmed: true, backup: 'make', expires_in_s: 1 }),
    ).toEqual({
      planHash: 'h',
      backup: 'make',
      expiresInS: 1,
    });
    expect(parseConfirmed({ plan_hash: 'h', confirmed: false })).toBeNull();
    expect(parseOutcome(OUTCOME)?.backupPath).toBe(OUTCOME.backup_path);
    expect(parseOutcome({ ...OUTCOME, result: 'maybe' })).toBeNull();
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

  it('shows the plan preview with its SQL, hash and warnings', async () => {
    const user = userEvent.setup();
    const { view } = setup();
    await user.click(await screen.findByRole('button', { name: 'Show plan' }));
    const preview = await screen.findByTestId('schema-designer-preview');
    expect(within(preview).getAllByRole('status')[0]).toHaveTextContent(
      'Tabrion plans 1 step. Nothing runs until you confirm the plan and run it.',
    );
    expect(screen.getByTestId('schema-designer-plan-hash')).toHaveTextContent(PREVIEW.plan_hash);
    expect(screen.getByTestId('schema-designer-base-changed')).toBeInTheDocument();
    const step = screen.getByTestId('schema-designer-step');
    expect(step).toHaveTextContent('Add column main.parts');
    expect(step).toHaveTextContent('changes data');
    expect(step).toHaveTextContent('ALTER TABLE parts ADD COLUMN qty INTEGER DEFAULT (0)');
    const warnings = screen.getAllByTestId('schema-designer-warning');
    expect(warnings.map((w) => w.dataset.code)).toEqual([
      'not_modelled',
      'sqlite_rowid',
      'fingerprint_gaps',
    ]);
    expect(warnings[1]).toHaveTextContent('Table parts is rebuilt and has no INTEGER PRIMARY KEY');
    // Before the confirmation no button runs the plan.
    expect(screen.queryByRole('button', { name: /run the plan|dry run/i })).toBeNull();
    expect(await serious(view.container)).toEqual([]);
  });

  it('says why a plan is not run and offers no confirmation', async () => {
    const user = userEvent.setup();
    setup({ preview: { status: 200, body: BLOCKED } });
    await user.click(await screen.findByRole('button', { name: 'Show plan' }));
    const blocked = await screen.findByTestId('schema-designer-blocked');
    expect(blocked).toHaveTextContent('Studio does not run this plan');
    expect(blocked).toHaveTextContent('migrations manage it');
    expect(blocked).toHaveTextContent('alembic.ini');
    expect(screen.queryByTestId('schema-designer-run')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Confirm' })).toBeNull();
  });

  it('writes the plan as an Alembic revision and says the revision does not check its source (STU-XSD-3)', async () => {
    const user = userEvent.setup();
    const { calls, view } = setup({ preview: { status: 200, body: BLOCKED } });
    await user.click(await screen.findByRole('button', { name: 'Show plan' }));
    const form = await screen.findByTestId('schema-designer-revision');
    expect(within(form).getByTestId('schema-designer-revision-source')).toHaveTextContent(
      PREVIEW.source_fingerprint,
    );
    const write = within(form).getByRole('button', { name: 'Write revision' });
    expect(write).toBeDisabled();
    await user.type(
      within(form).getByLabelText('Revision message (at most 200 characters, one line)'),
      'add qty',
    );
    expect(await serious(view.container)).toEqual([]);
    await user.click(write);
    const written = await screen.findByTestId('schema-designer-revision-written');
    expect(written).toHaveTextContent(
      'Revision 0123456789ab written to migrations/versions/0123456789ab_add_qty.py, after 0001.',
    );
    expect(written).toHaveTextContent('trust the code again');
    expect(calls.find((c) => c.url.endsWith('/schema-designer/revision'))?.body).toEqual({
      connection_id: 'c_1',
      plan_hash: PREVIEW.plan_hash,
      message: 'add qty',
    });
  });

  it('says why no revision is written, before and after a refused write', async () => {
    const user = userEvent.setup();
    setup();
    await user.click(await screen.findByRole('button', { name: 'Show plan' }));
    expect(await screen.findByTestId('schema-designer-revision-blocked')).toHaveTextContent(
      'The project has no alembic.ini with a script_location.',
    );
    expect(screen.queryByRole('button', { name: 'Write revision' })).toBeNull();
  });

  it('shows a refused revision with its reason', async () => {
    const user = userEvent.setup();
    setup({
      preview: { status: 200, body: BLOCKED },
      revision: {
        status: 400,
        body: {
          code: 'studio.schema_revision_not_allowed',
          kind: 'request',
          status: 400,
          title: 'x',
          detail: 'x',
          issues: [{ pointer: 'plan.reason', code: 'plan.code_untrusted' }],
        },
      },
    });
    await user.click(await screen.findByRole('button', { name: 'Show plan' }));
    const form = await screen.findByTestId('schema-designer-revision');
    await user.type(
      within(form).getByLabelText('Revision message (at most 200 characters, one line)'),
      'x',
    );
    await user.click(within(form).getByRole('button', { name: 'Write revision' }));
    expect(await within(form).findByRole('alert')).toHaveTextContent('No revision was written');
    expect(form).toHaveTextContent('code is not trusted');
  });

  it('confirms with what was typed and ticked, then runs once with the recorded hash', async () => {
    const user = userEvent.setup();
    const { calls, view } = setup();
    await user.click(await screen.findByRole('button', { name: 'Show plan' }));
    const form = await screen.findByTestId('schema-designer-run');
    expect(screen.getByTestId('schema-designer-run-backup')).toHaveTextContent(
      'A copy of the database file is made before the run.',
    );
    await user.type(within(form).getByLabelText('Type the write profile name: parts-w'), 'parts-w');
    await user.type(within(form).getByLabelText('Type the table name: parts'), 'parts');
    await user.click(within(form).getByLabelText('I accept that step 1 changes or removes data.'));
    expect(await serious(view.container)).toEqual([]);
    await user.click(within(form).getByRole('button', { name: 'Confirm' }));
    await screen.findByTestId('schema-designer-confirmed');
    expect(calls.find((c) => c.url.endsWith('/plan/confirm'))?.body).toEqual({
      connection_id: 'c_1',
      plan_hash: PREVIEW.plan_hash,
      typed: 'parts-w',
      tables: ['parts'],
      steps: [0],
      locking: false,
      backup: 'make',
    });
    const go = screen.getByRole('button', { name: 'Run the plan' });
    expect(go).not.toHaveFocus();
    expect(screen.getByRole('button', { name: 'Dry run' })).not.toHaveFocus();
    await user.click(go);
    const outcome = await screen.findByTestId('schema-designer-outcome');
    expect(outcome).toHaveTextContent('Done: 1 step run and committed.');
    expect(screen.getByTestId('schema-designer-backup-path')).toHaveTextContent(
      OUTCOME.backup_path,
    );
    expect(calls.find((c) => c.url.endsWith('/plan/run'))?.body).toEqual({
      connection_id: 'c_1',
      plan_hash: PREVIEW.plan_hash,
      dry_run: false,
    });
    expect(screen.queryByTestId('schema-designer-preview')).toBeNull();
    // The draft is read again after the run.
    await waitFor(() =>
      expect(calls.filter((c) => c.url.endsWith('/draft/view')).length).toBeGreaterThan(1),
    );
    for (const call of calls) {
      for (const key of ['model', 'snapshot', 'plan', 'target', 'environment']) {
        expect(call.body ?? {}).not.toHaveProperty(key);
      }
    }
  });

  it('leaves the backup out on DEV only with the typed file name, and dry-runs on request', async () => {
    const user = userEvent.setup();
    const { calls } = setup();
    await user.click(await screen.findByRole('button', { name: 'Show plan' }));
    const form = await screen.findByTestId('schema-designer-run');
    await user.type(within(form).getByLabelText('Type the write profile name: parts-w'), 'parts-w');
    await user.type(within(form).getByLabelText('Type the table name: parts'), 'parts');
    await user.click(within(form).getByLabelText('I accept that step 1 changes or removes data.'));
    await user.click(within(form).getByLabelText(/Leave the backup out/));
    await user.type(
      within(form).getByLabelText('Type the database file name to leave the backup out: parts.db'),
      'parts.db',
    );
    await user.click(within(form).getByRole('button', { name: 'Confirm' }));
    await screen.findByTestId('schema-designer-confirmed');
    expect(calls.find((c) => c.url.endsWith('/plan/confirm'))?.body).toMatchObject({
      backup: 'skip',
      backup_typed: 'parts.db',
    });
    await user.click(screen.getByRole('button', { name: 'Dry run' }));
    expect(await screen.findByTestId('schema-designer-outcome')).toHaveTextContent(
      'Dry run: 1 step run and rolled back. Nothing was changed.',
    );
    expect(calls.find((c) => c.url.endsWith('/plan/run'))?.body).toMatchObject({ dry_run: true });
  });

  it('offers no opt-out where the server allows none', async () => {
    const user = userEvent.setup();
    setup({
      preview: {
        status: 200,
        body: {
          ...PREVIEW,
          environment: 'test',
          confirmation: {
            ...PREVIEW.confirmation,
            backup: {
              required: true,
              engine: 'sqlite',
              opt_out_allowed: false,
              opt_out_text: null,
            },
          },
        },
      },
    });
    await user.click(await screen.findByRole('button', { name: 'Show plan' }));
    await screen.findByTestId('schema-designer-run');
    expect(screen.queryByTestId('schema-designer-run-skip')).toBeNull();
  });

  it('says while a PostgreSQL run is under way that it is making the backup first', async () => {
    const user = userEvent.setup();
    let release: () => void = () => undefined;
    const runGate = new Promise<void>((resolve) => {
      release = resolve;
    });
    setup({
      runGate,
      preview: {
        status: 200,
        body: {
          ...PREVIEW,
          confirmation: {
            ...PREVIEW.confirmation,
            backup: {
              required: true,
              engine: 'postgresql',
              opt_out_allowed: false,
              opt_out_text: null,
            },
          },
        },
      },
    });
    await user.click(await screen.findByRole('button', { name: 'Show plan' }));
    const form = await screen.findByTestId('schema-designer-run');
    await user.type(within(form).getByLabelText('Type the write profile name: parts-w'), 'parts-w');
    await user.type(within(form).getByLabelText('Type the table name: parts'), 'parts');
    await user.click(within(form).getByLabelText('I accept that step 1 changes or removes data.'));
    await user.click(within(form).getByRole('button', { name: 'Confirm' }));
    await screen.findByTestId('schema-designer-confirmed');
    expect(screen.queryByTestId('schema-designer-running')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Run the plan' }));
    const running = await screen.findByTestId('schema-designer-running');
    expect(running).toHaveAttribute('role', 'status');
    expect(running).toHaveTextContent('Making the backup of the whole database with pg_dump');
    expect(form).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByRole('button', { name: 'Run the plan' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Dry run' })).toBeDisabled();
    release();
    await screen.findByTestId('schema-designer-outcome');
    expect(screen.queryByTestId('schema-designer-running')).toBeNull();
  });

  it('says that a PostgreSQL run starts only after its pg_dump backup, with no opt-out', async () => {
    const user = userEvent.setup();
    setup({
      preview: {
        status: 200,
        body: {
          ...PREVIEW,
          confirmation: {
            ...PREVIEW.confirmation,
            backup: {
              required: true,
              engine: 'postgresql',
              opt_out_allowed: false,
              opt_out_text: null,
            },
          },
        },
      },
    });
    await user.click(await screen.findByRole('button', { name: 'Show plan' }));
    await screen.findByTestId('schema-designer-run');
    const backup = screen.getByTestId('schema-designer-run-backup');
    expect(backup).toHaveTextContent('A pg_dump archive of the whole database is made');
    expect(backup).toHaveTextContent('The run starts only when that backup succeeded.');
    expect(screen.queryByTestId('schema-designer-run-skip')).toBeNull();
  });

  it('shows a refused run and drops the used-up preview', async () => {
    const user = userEvent.setup();
    setup({
      run: {
        status: 400,
        body: {
          code: 'studio.schema_run_check_failed',
          kind: 'request',
          status: 400,
          title: 'x',
          detail: 'x',
          issues: [{ pointer: 'plan.check', code: 'plan.foreign_key_check' }],
        },
      },
    });
    await user.click(await screen.findByRole('button', { name: 'Show plan' }));
    const form = await screen.findByTestId('schema-designer-run');
    await user.type(within(form).getByLabelText('Type the write profile name: parts-w'), 'parts-w');
    await user.type(within(form).getByLabelText('Type the table name: parts'), 'parts');
    await user.click(within(form).getByLabelText('I accept that step 1 changes or removes data.'));
    await user.click(within(form).getByRole('button', { name: 'Confirm' }));
    await user.click(await screen.findByRole('button', { name: 'Run the plan' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('A check before the commit failed');
    expect(screen.getByTestId('schema-designer-plan-issues')).toHaveTextContent(
      'A row breaks a foreign key (foreign_key_check).',
    );
    expect(screen.queryByTestId('schema-designer-preview')).toBeNull();
  });

  it('names the table of a refused preview from its position', async () => {
    const user = userEvent.setup();
    setup({
      preview: {
        status: 400,
        body: {
          code: 'studio.schema_plan_refused',
          kind: 'request',
          status: 400,
          title: 'x',
          detail: 'x',
          issues: [
            { pointer: 'plan.reason', code: 'plan.dependent_object' },
            { pointer: 'tables.0', code: 'plan.dependent_object' },
          ],
        },
      },
    });
    await user.click(await screen.findByRole('button', { name: 'Show plan' }));
    expect(await screen.findByTestId('schema-designer-plan-table')).toHaveTextContent(
      'Table: parts',
    );
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
