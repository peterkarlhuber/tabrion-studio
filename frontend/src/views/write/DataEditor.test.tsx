/**
 * STU-026: the Data Editor page against a fake server. Unlock (D-03), edit, preview, typed confirmation, one run; a
 * conflict says so and offers a reload; the changes never reach the drafts, the history or browser storage (N-3).
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { DataEditor, EDITOR_ROWS } from './DataEditor';

const HASH = 'c'.repeat(64);
const SECRET = 'canary-7f3a';

type Call = { method: string; url: string; body: unknown };

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function page(rows: unknown[][]) {
  return {
    connection_id: 'c1',
    schema: 'main',
    name: 'parts',
    limit: EDITOR_ROWS,
    sort: null,
    desc: false,
    columns: [
      { name: 'id', type: 'INTEGER' },
      { name: 'name', type: 'TEXT' },
      { name: 'qty', type: 'INTEGER' },
    ],
    rows,
    truncated_cells: [],
    page: 'p1',
    next: null,
    paging: 'primary_key',
  };
}

function server(
  options: { unlocked?: boolean; execute?: () => Response; sideEffects?: boolean } = {},
) {
  const triggers = options.sideEffects
    ? [{ name: 'parts_audit', timing: 'AFTER', events: ['UPDATE'], enabled: true }]
    : [];
  const rules = options.sideEffects ? [{ name: 'parts_log', event: 'UPDATE', instead: false }] : [];
  const calls: Call[] = [];
  let unlocked = options.unlocked ?? false;
  let rows: unknown[][] = [
    [1, 'bolt', 3],
    [2, 'nut', null],
  ];
  const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const body: unknown = typeof init?.body === 'string' ? JSON.parse(init.body) : null;
    calls.push({ method, url, body });
    if (url === '/api/write/unlocks' && method === 'GET') {
      return json({
        unlocks: unlocked
          ? [{ connection_id: 'c1', write_profile_id: 'write:parts-w', environment: 'dev' }]
          : [],
      });
    }
    if (url === '/api/write/unlocks' && method === 'POST') {
      unlocked = true;
      return json({ connection_id: 'c1' });
    }
    if (url === '/api/write/unlocks' && method === 'DELETE') {
      unlocked = false;
      return new Response(null, { status: 204 });
    }
    if (url === '/api/write/profiles') {
      return json({
        write_profiles: [
          { id: 'write:parts-w', name: 'parts-w', kind: 'write', read_profile_id: 'user:parts' },
          { id: 'write:other', name: 'other', kind: 'write', read_profile_id: 'user:other' },
          { id: 'admin:parts-a', name: 'parts-a', kind: 'admin', read_profile_id: 'user:parts' },
        ],
      });
    }
    if (url === '/api/write/data-editor/table') {
      return json({
        connection_id: 'c1',
        schema: 'main',
        table: 'parts',
        columns: ['id', 'name', 'qty'],
        primary_key: ['id'],
        triggers,
        rules,
        row_security: options.sideEffects === true,
        max_changes: 1000,
      });
    }
    if (url.startsWith('/api/connections/c1/rows?')) return json(page(rows));
    if (url === '/api/write/plans' && method === 'POST') {
      return json({
        plan_hash: HASH,
        confirm_text: 'parts-w',
        destructive: false,
        risks: [],
        steps: [{ action: 'row.update', target: 'main.parts', destructive: false, rows: 1 }],
        targets: [{ schema: 'main', table: 'parts', triggers, rules }],
      });
    }
    if (url === '/api/write/plans/confirm') return json({ plan_hash: HASH, confirmed: true });
    if (url === '/api/write/plans/execute') {
      if (options.execute) return options.execute();
      rows = [
        [1, 'bolt', 3],
        [2, 'nut', 5],
      ];
      return json({ plan_hash: HASH, rows: { updated: 1 }, audit_pending: false });
    }
    if (url === '/api/write/plans' && method === 'DELETE')
      return new Response(null, { status: 204 });
    return new Response(null, { status: 404 });
  }) as typeof fetch;
  return { fetcher, calls };
}

function show(fetcher: typeof fetch) {
  render(
    <DataEditor
      fetcher={fetcher}
      connectionId="c1"
      profileId="user:parts"
      schema="main"
      name="parts"
    />,
  );
}

async function editQty(user: ReturnType<typeof userEvent.setup>, value: string) {
  const cell = await screen.findByRole('textbox', { name: 'Row 2, qty' });
  await user.type(cell, value);
  return cell;
}

describe('DataEditor (STU-026)', () => {
  it('a table with triggers, rules or row security gets a notice, not a block (owner decision 2026-11-15)', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server({ unlocked: true, sideEffects: true });
    show(fetcher);
    const notice = await screen.findByTestId('data-editor-side-effects');
    expect(notice).toHaveAttribute('role', 'note');
    expect(notice).toHaveTextContent('triggers or rules: parts_audit, parts_log.');
    expect(notice).toHaveTextContent('You can still edit the table');
    expect(screen.getByTestId('data-editor-row-security')).toHaveTextContent('Row security is on');
    expect(screen.getByTestId('data-editor-help')).toHaveTextContent('nothing is saved until');
    await editQty(user, '5');
    await user.click(screen.getByRole('button', { name: 'Preview' }));
    const review = await screen.findByTestId('data-editor-review');
    expect(review).toHaveTextContent('Triggers and rules also run: parts_audit, parts_log.');
    await user.type(within(review).getByLabelText('Type parts-w to confirm'), 'parts-w');
    await user.click(within(review).getByRole('button', { name: 'Confirm' }));
    await user.click(await screen.findByTestId('data-editor-execute'));
    expect(await screen.findByTestId('data-editor-saved')).toHaveTextContent('Saved: 1 updated');
    expect(calls.filter((c) => c.url === '/api/write/plans/execute')).toHaveLength(1);
  });

  it('a table without triggers, rules or row security gets no notice', async () => {
    const { fetcher } = server({ unlocked: true });
    show(fetcher);
    await screen.findByTestId('data-editor-grid');
    expect(screen.queryByTestId('data-editor-side-effects')).toBeNull();
    expect(screen.queryByTestId('data-editor-row-security')).toBeNull();
  });

  it('asks for the unlock with the coupled write profile only, then shows the grid', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server();
    show(fetcher);
    const form = await screen.findByTestId('data-editor-unlock');
    // One coupled write profile: no choice, its name is the text to type (D-03).
    expect(within(form).queryByRole('combobox')).toBeNull();
    const submit = within(form).getByRole('button', { name: 'Unlock' });
    expect(submit).toBeDisabled();
    await user.type(
      within(form).getByRole('textbox', { name: 'Type parts-w to unlock' }),
      'parts-w',
    );
    expect(submit).toBeEnabled();
    await user.click(submit);
    expect(await screen.findByTestId('data-editor-grid')).toBeVisible();
    expect(calls.find((c) => c.method === 'POST' && c.url === '/api/write/unlocks')?.body).toEqual({
      connection_id: 'c1',
      write_profile_id: 'write:parts-w',
      typed_name: 'parts-w',
    });
  });

  it('edits a NULL cell, previews, confirms by typing and saves in one run', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server({ unlocked: true });
    show(fetcher);
    const cell = await editQty(user, '5');
    expect(cell).toHaveValue('5');
    expect(screen.getByTestId('data-editor-pending')).toHaveTextContent('1 change');
    await user.click(screen.getByRole('button', { name: 'Preview' }));
    const plan = calls.find((c) => c.url === '/api/write/plans' && c.method === 'POST');
    expect(plan?.body).toEqual({
      connection_id: 'c1',
      executor: 'data_editor.changeset',
      request: {
        schema: 'main',
        table: 'parts',
        columns: ['id', 'name', 'qty'],
        changes: [
          {
            op: 'update',
            key: { id: 2 },
            old: { id: 2, name: 'nut', qty: null },
            new: { qty: '5' },
          },
        ],
      },
    });
    const confirm = await screen.findByRole('button', { name: 'Confirm' });
    expect(confirm).toBeDisabled();
    await user.type(screen.getByRole('textbox', { name: 'Type parts-w to confirm' }), 'parts-w');
    await user.click(confirm);
    await user.click(await screen.findByTestId('data-editor-execute'));
    expect(await screen.findByTestId('data-editor-saved')).toHaveTextContent(
      'Saved: 1 updated, 0 inserted, 0 deleted.',
    );
    expect(await screen.findByRole('textbox', { name: 'Row 2, qty' })).toHaveValue('5');
    expect(screen.getByTestId('data-editor-pending')).toHaveTextContent('0 changes');
    expect(calls.filter((c) => c.url.startsWith('/api/write/plans/')).map((c) => c.url)).toEqual([
      '/api/write/plans/confirm',
      '/api/write/plans/execute',
    ]);
  });

  it('adds and deletes rows; a delete is listed in the summary', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server({ unlocked: true });
    show(fetcher);
    await screen.findByTestId('data-editor-grid');
    await user.click(screen.getByRole('button', { name: 'Add row' }));
    await user.type(screen.getByRole('textbox', { name: 'New row 1, name' }), 'pin');
    const deletes = screen.getAllByRole('button', { name: 'Delete row' });
    await user.click(deletes[0] as HTMLElement);
    const overview = screen.getByTestId('data-editor-changes');
    expect(overview).toHaveTextContent('Rows: 0 changed, 1 new, 1 deleted');
    expect(overview).toHaveTextContent('New row 1');
    expect(overview).toHaveTextContent('Delete the row id = 1');
    await user.click(screen.getByRole('button', { name: 'Preview' }));
    await screen.findByRole('button', { name: 'Confirm' });
    const plan = calls.find((c) => c.url === '/api/write/plans' && c.method === 'POST');
    expect((plan?.body as { request: { changes: unknown[] } }).request.changes).toEqual([
      { op: 'insert', values: { name: 'pin' } },
      { op: 'delete', key: { id: 1 }, old: { id: 1, name: 'bolt', qty: 3 } },
    ]);
    // Back drops the plan on the server.
    await user.click(screen.getByRole('button', { name: 'Back' }));
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'DELETE' && c.url === '/api/write/plans')).toBe(true),
    );
  });

  it('a conflict says nothing was saved, keeps the edits and offers a reload', async () => {
    const user = userEvent.setup();
    const { fetcher } = server({
      unlocked: true,
      execute: () =>
        json({ code: 'studio.data_editor_conflict', kind: 'request', status: 400 }, 400),
    });
    show(fetcher);
    await editQty(user, '9');
    await user.click(screen.getByRole('button', { name: 'Preview' }));
    await user.type(
      await screen.findByRole('textbox', { name: 'Type parts-w to confirm' }),
      'parts-w',
    );
    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    await user.click(await screen.findByTestId('data-editor-execute'));
    expect(await screen.findByText(/Nothing was saved/)).toBeVisible();
    expect(screen.getByRole('textbox', { name: 'Row 2, qty' })).toHaveValue('9');
    await user.click(screen.getByRole('button', { name: 'Reload the rows' }));
    expect(await screen.findByRole('textbox', { name: 'Row 2, qty' })).toHaveValue('');
  });

  it('never sends the changes to the drafts or the history, nor keeps them in browser storage (N-3)', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server({ unlocked: true });
    window.localStorage.clear();
    window.sessionStorage.clear();
    show(fetcher);
    const cell = await screen.findByRole('textbox', { name: 'Row 1, name' });
    await user.clear(cell);
    await user.type(cell, SECRET);
    await user.click(screen.getByRole('button', { name: 'Preview' }));
    await screen.findByRole('button', { name: 'Confirm' });
    expect(calls.filter((c) => /\/api\/(drafts|history)/.test(c.url))).toEqual([]);
    const sent = calls.filter((c) => JSON.stringify(c.body ?? '').includes(SECRET));
    expect(sent.map((c) => c.url)).toEqual(['/api/write/plans']);
    const stored = [window.localStorage, window.sessionStorage].flatMap((storage) =>
      Object.keys(storage).map((key) => `${key}=${storage.getItem(key) ?? ''}`),
    );
    expect(stored.filter((entry) => entry.includes(SECRET))).toEqual([]);
  });

  it('locks again on request', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server({ unlocked: true });
    show(fetcher);
    await screen.findByTestId('data-editor-grid');
    await user.click(screen.getByRole('button', { name: 'Lock' }));
    expect(await screen.findByTestId('data-editor-unlock')).toBeVisible();
    expect(calls.some((c) => c.method === 'DELETE' && c.url === '/api/write/unlocks')).toBe(true);
  });
});

// --- STU-XDE-1: type editors, checks at the cell, the change overview -----------------------------------------------

const TYPED_COLUMNS = ['id', 'name', 'ok', 'mood', 'price', 'doc'];

function typedServer(options: { plan?: (body: unknown) => Response } = {}) {
  const calls: Call[] = [];
  const kind = (name: string, extra: Record<string, unknown>) => ({
    name,
    nullable: true,
    optional: true,
    read_only: false,
    array: false,
    ...extra,
  });
  const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const body: unknown = typeof init?.body === 'string' ? JSON.parse(init.body) : null;
    calls.push({ method, url, body });
    if (url === '/api/write/unlocks' && method === 'GET') {
      return json({ unlocks: [{ connection_id: 'c1', write_profile_id: 'write:w' }] });
    }
    if (url === '/api/write/data-editor/table') {
      return json({
        connection_id: 'c1',
        schema: 'app',
        table: 'parts',
        columns: TYPED_COLUMNS,
        column_types: [
          kind('id', { kind: 'int4', nullable: false }),
          kind('name', { kind: 'text', length: 5, nullable: false, optional: false }),
          kind('ok', { kind: 'boolean' }),
          kind('mood', { kind: 'enum', values: ['ok', 'bad'] }),
          kind('price', { kind: 'numeric', precision: 5, scale: 2 }),
          kind('doc', { kind: 'json' }),
        ],
        primary_key: ['id'],
        triggers: [],
        rules: [],
        row_security: false,
        max_changes: 1000,
      });
    }
    if (url.startsWith('/api/connections/c1/rows?')) {
      return json({
        ...page([
          [1, 'bolt', true, 'ok', '1.50', { a: 1 }],
          [2, 'nut', null, null, null, null],
        ]),
        schema: 'app',
        columns: TYPED_COLUMNS.map((name) => ({ name, type: '' })),
      });
    }
    if (url === '/api/write/plans' && method === 'POST') {
      if (options.plan) return options.plan(body);
      return json({ plan_hash: HASH, confirm_text: 'w', destructive: false, risks: [], steps: [] });
    }
    return new Response(null, { status: 404 });
  }) as typeof fetch;
  return { fetcher, calls };
}

function showTyped(fetcher: typeof fetch) {
  render(
    <DataEditor fetcher={fetcher} connectionId="c1" profileId="user:p" schema="app" name="parts" />,
  );
}

describe('DataEditor type editors (STU-XDE-1)', () => {
  it('edits booleans and enums by choice, decimals and JSON as text, and sends the column values', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = typedServer();
    showTyped(fetcher);
    const ok = await screen.findByRole('combobox', { name: 'Row 2, ok' });
    expect([...(ok as HTMLSelectElement).options].map((option) => option.text)).toEqual([
      'NULL',
      'true',
      'false',
    ]);
    await user.selectOptions(ok, 'false');
    await user.selectOptions(screen.getByRole('combobox', { name: 'Row 2, mood' }), 'bad');
    await user.type(screen.getByRole('textbox', { name: 'Row 2, price' }), '12.5');
    await user.click(screen.getByRole('textbox', { name: 'Row 2, doc' }));
    await user.paste('{"b": [1, 2]}');
    // The header names the type and its modifiers.
    expect(screen.getByRole('columnheader', { name: /price/ })).toHaveTextContent('numeric(5,2)');
    const overview = screen.getByTestId('data-editor-changes');
    expect(overview).toHaveTextContent('Change the row id = 2');
    const rows = within(overview)
      .getAllByRole('row')
      .map((row) => row.textContent);
    expect(rows).toEqual([
      'ColumnBeforeAfter',
      'okNULLfalse',
      'moodNULLbad',
      'priceNULL12.5',
      'docNULL{"b":[1,2]}',
    ]);
    await user.click(screen.getByRole('button', { name: 'Preview' }));
    await screen.findByRole('button', { name: 'Confirm' });
    const plan = calls.find((c) => c.url === '/api/write/plans' && c.method === 'POST');
    expect((plan?.body as { request: { changes: unknown[] } }).request.changes).toEqual([
      {
        op: 'update',
        key: { id: 2 },
        old: { id: 2, name: 'nut', ok: null, mood: null, price: null, doc: null },
        new: { ok: false, mood: 'bad', price: '12.5', doc: { b: [1, 2] } },
      },
    ]);
    // The review shows the same overview.
    expect(screen.getByTestId('data-editor-review-changes')).toHaveTextContent('moodNULLbad');
  });

  it('marks a value that does not fit at its cell and holds the preview until it is fixed', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = typedServer();
    showTyped(fetcher);
    const name = await screen.findByRole('textbox', { name: 'Row 1, name' });
    await user.type(name, 'xx');
    expect(name).toHaveAttribute('aria-invalid', 'true');
    expect(name).toHaveAccessibleDescription('Too long: at most 5 characters.');
    const price = screen.getByRole('textbox', { name: 'Row 1, price' });
    await user.clear(price);
    await user.type(price, '1234');
    expect(price).toHaveAccessibleDescription(/Out of the range of this column/);
    const preview = screen.getByRole('button', { name: 'Preview' });
    expect(preview).toBeDisabled();
    expect(screen.getByTestId('data-editor-issues')).toHaveTextContent(
      '2 values do not fit their columns.',
    );
    await user.clear(name);
    await user.type(name, 'pin');
    await user.clear(price);
    await user.type(price, '999.99');
    expect(preview).toBeEnabled();
    expect(screen.queryByTestId('data-editor-issues')).toBeNull();
    expect(calls.some((c) => c.url === '/api/write/plans')).toBe(false);
  });

  it('tells NULL from the empty text, offers NULL only where the column takes it, and names required columns', async () => {
    const user = userEvent.setup();
    const { fetcher } = typedServer();
    showTyped(fetcher);
    const name = await screen.findByRole('textbox', { name: 'Row 1, name' });
    expect(screen.queryByRole('button', { name: 'Set Row 1, name to NULL' })).toBeNull();
    await user.clear(name);
    expect(screen.getByTestId('data-editor-changes')).toHaveTextContent('namebolt(empty text)');
    await user.click(screen.getByRole('button', { name: 'Set Row 1, price to NULL' }));
    expect(screen.getByTestId('data-editor-changes')).toHaveTextContent('price1.50NULL');
    await user.click(screen.getByRole('button', { name: 'Add row' }));
    expect(screen.getByRole('textbox', { name: 'New row 1, name' })).toHaveAccessibleDescription(
      'Required: this column has no default and takes no NULL.',
    );
    expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();
    // A choice in a new row starts at the column's default.
    expect(screen.getByRole('combobox', { name: 'New row 1, ok' })).toHaveDisplayValue('default');
  });

  it("places the server's refusal at the cell, without echoing a value", async () => {
    const user = userEvent.setup();
    const { fetcher } = typedServer({
      plan: () =>
        json(
          {
            code: 'studio.data_editor_value_invalid',
            kind: 'request',
            status: 400,
            issues: [{ pointer: 'changes.0.4', code: 'value.out_of_range' }],
          },
          400,
        ),
    });
    showTyped(fetcher);
    const price = await screen.findByRole('textbox', { name: 'Row 2, price' });
    await user.type(price, '1');
    await user.click(screen.getByRole('button', { name: 'Preview' }));
    expect(await screen.findByTestId('data-editor-error')).toHaveTextContent(
      'the cells in question are marked',
    );
    expect(price).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByTestId('data-editor-issues')).toHaveTextContent(
      'Row 2, column price: Out of the range of this column',
    );
    // The next edit clears the server's marks.
    await user.type(price, '0');
    expect(price).not.toHaveAttribute('aria-invalid');
  });
});

// --- G-S2 6A: bytes replaced by a file, constraint refusals at the cell, more rows ---------------------------------

const FILE_COLUMNS = ['id', 'code', 'raw'];

/**
 * A table of `id`, a unique `code` and a bytes column `raw` whose rows come in pages of two (`next` is the offset of
 * the next page). `execute` answers the run.
 */
function fileServer(options: { execute?: (body: unknown) => Response; rows?: number } = {}) {
  const calls: Call[] = [];
  const all: unknown[][] = Array.from({ length: options.rows ?? 5 }, (_, at) => [
    at + 1,
    `c${at + 1}`,
    at === 0 ? { base64: 'AQI=' } : null,
  ]);
  const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const body: unknown = typeof init?.body === 'string' ? JSON.parse(init.body) : null;
    calls.push({ method, url, body });
    if (url === '/api/write/unlocks' && method === 'GET') {
      return json({ unlocks: [{ connection_id: 'c1', write_profile_id: 'write:w' }] });
    }
    if (url === '/api/write/data-editor/table') {
      const type = (name: string, kind: string, extra: Record<string, unknown> = {}) => ({
        name,
        kind,
        nullable: true,
        optional: true,
        read_only: false,
        array: false,
        ...extra,
      });
      return json({
        connection_id: 'c1',
        schema: 'app',
        table: 'files',
        columns: FILE_COLUMNS,
        column_types: [
          type('id', 'int4', { nullable: false }),
          type('code', 'text'),
          type('raw', 'bytes'),
        ],
        primary_key: ['id'],
        triggers: [],
        rules: [],
        row_security: false,
        max_changes: 1000,
      });
    }
    if (url.startsWith('/api/connections/c1/rows?')) {
      const after = Number(new URL(url, 'http://test').searchParams.get('after') ?? '0');
      const next = after + 2 < all.length ? String(after + 2) : null;
      return json({
        ...page(all.slice(after, after + 2)),
        schema: 'app',
        name: 'files',
        columns: FILE_COLUMNS.map((name) => ({ name, type: '' })),
        next,
      });
    }
    if (url === '/api/write/plans' && method === 'POST') {
      return json({ plan_hash: HASH, confirm_text: 'w', destructive: false, risks: [], steps: [] });
    }
    if (url === '/api/write/plans/confirm') return json({ plan_hash: HASH, confirmed: true });
    if (url === '/api/write/plans/execute') {
      return options.execute?.(body) ?? json({ plan_hash: HASH, rows: { updated: 1 } });
    }
    return new Response(null, { status: 404 });
  }) as typeof fetch;
  return { fetcher, calls };
}

function showFiles(fetcher: typeof fetch) {
  render(
    <DataEditor fetcher={fetcher} connectionId="c1" profileId="user:p" schema="app" name="files" />,
  );
}

const sentChanges = (calls: Call[]) =>
  (
    calls.filter((c) => c.url === '/api/write/plans' && c.method === 'POST').at(-1)?.body as {
      request: { changes: unknown[] };
    }
  ).request.changes;

async function save(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Preview' }));
  await user.type(await screen.findByLabelText('Type w to confirm'), 'w');
  await user.click(screen.getByRole('button', { name: 'Confirm' }));
  await user.click(await screen.findByTestId('data-editor-execute'));
}

describe('DataEditor bytes, constraints and more rows (G-S2 6A)', () => {
  it('replaces a bytes value by the content of a picked file, shown by its size, never typed', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = fileServer();
    showFiles(fetcher);
    const replace = await screen.findByRole('button', { name: 'Replace Row 1, raw with a file' });
    expect(replace).toHaveAccessibleDescription('2 bytes');
    expect(screen.queryByRole('textbox', { name: 'Row 1, raw' })).toBeNull();
    const input = screen.getByLabelText<HTMLInputElement>('Row 1, raw');
    await user.upload(input, new File([new Uint8Array([7, 8, 9])], 'part.bin'));
    await waitFor(() => expect(replace).toHaveAccessibleDescription('3 bytes'));
    expect(screen.getByTestId('data-editor-changes')).toHaveTextContent('raw2 bytes3 bytes');
    await user.click(screen.getByRole('button', { name: 'Preview' }));
    await screen.findByRole('button', { name: 'Confirm' });
    expect(sentChanges(calls)).toEqual([
      {
        op: 'update',
        key: { id: 1 },
        old: { id: 1, code: 'c1', raw: { base64: 'AQI=' } },
        new: { raw: { base64: 'BwgJ' } },
      },
    ]);
  });

  it('marks a file above the limit at its cell, does not read it and holds the preview', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = fileServer();
    showFiles(fetcher);
    await screen.findByTestId('data-editor-grid');
    const file = new File([new Uint8Array(64 * 1024 + 1)], 'big.bin');
    const read = vi.spyOn(file, 'arrayBuffer');
    await user.upload(screen.getByLabelText<HTMLInputElement>('Row 2, raw'), file);
    const replace = screen.getByRole('button', { name: 'Replace Row 2, raw with a file' });
    expect(replace).toHaveAttribute('aria-invalid', 'true');
    expect(replace).toHaveAccessibleDescription(/65537 bytes Too large: at most 64 KiB\./);
    expect(read).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();
    // NULL instead: fine again.
    await user.click(screen.getByRole('button', { name: 'Set Row 2, raw to NULL' }));
    expect(replace).not.toHaveAttribute('aria-invalid');
    expect(calls.some((c) => c.url === '/api/write/plans')).toBe(false);
  });

  it("places the run's constraint refusal at its cell or row; nothing is saved and the edits stay", async () => {
    const user = userEvent.setup();
    const { fetcher } = fileServer({
      execute: () =>
        json(
          {
            code: 'studio.data_editor_constraint',
            kind: 'database',
            status: 400,
            issues: [
              { pointer: 'changes.0.1', code: 'constraint.unique' },
              { pointer: 'changes.1', code: 'constraint.foreign_key' },
            ],
          },
          400,
        ),
    });
    showFiles(fetcher);
    const code = await screen.findByRole('textbox', { name: 'Row 2, code' });
    await user.clear(code);
    await user.type(code, 'c1');
    const rows = within(screen.getByTestId('data-editor-grid')).getAllByRole('button', {
      name: 'Delete row',
    });
    await user.click(rows[0] as HTMLElement);
    await save(user);
    expect(await screen.findByTestId('data-editor-error')).toHaveTextContent(
      'Nothing was saved: all changes were rolled back.',
    );
    expect(code).toHaveAttribute('aria-invalid', 'true');
    expect(code).toHaveAccessibleDescription(
      /a value that must be unique is already in another row/,
    );
    expect(code).toHaveValue('c1');
    const keep = screen.getByRole('button', { name: 'Keep row' });
    expect(keep).toHaveAccessibleDescription(/a reference between rows would break/);
    const issues = screen.getByTestId('data-editor-issues');
    expect(issues).toHaveTextContent('2 changes need correcting before the next preview');
    expect(issues).toHaveTextContent('Row 2, column code: The database refused this change');
    expect(issues).toHaveTextContent('Row 1: The database refused this change');
    // The next edit clears the marks.
    await user.type(code, 'x');
    expect(code).not.toHaveAttribute('aria-invalid');
    expect(screen.queryByTestId('data-editor-issues')).toBeNull();
  });

  it('loads more rows after the loaded ones; an edit stays with its row and reaches the right key', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = fileServer();
    showFiles(fetcher);
    await screen.findByTestId('data-editor-grid');
    expect(screen.getByTestId('data-editor-loaded')).toHaveTextContent(
      '2 rows are loaded; the table has more.',
    );
    // An edit on the first page, then the next pages.
    const first = screen.getByRole('textbox', { name: 'Row 2, code' });
    await user.type(first, '!');
    await user.click(screen.getByRole('button', { name: 'Load 100 more rows' }));
    await screen.findByRole('textbox', { name: 'Row 4, code' });
    expect(first).toHaveValue('c2!');
    await user.click(screen.getByRole('button', { name: 'Load 100 more rows' }));
    const last = await screen.findByRole('textbox', { name: 'Row 5, code' });
    expect(screen.getByTestId('data-editor-loaded')).toHaveTextContent('All rows are loaded (5).');
    expect(screen.queryByRole('button', { name: 'Load 100 more rows' })).toBeNull();
    await user.type(last, '?');
    expect(screen.getByTestId('data-editor-pending')).toHaveTextContent('2 changes');
    await user.click(screen.getByRole('button', { name: 'Preview' }));
    await screen.findByRole('button', { name: 'Confirm' });
    expect(sentChanges(calls)).toEqual([
      { op: 'update', key: { id: 2 }, old: { id: 2, code: 'c2', raw: null }, new: { code: 'c2!' } },
      { op: 'update', key: { id: 5 }, old: { id: 5, code: 'c5', raw: null }, new: { code: 'c5?' } },
    ]);
    const asked = calls.filter((c) => c.url.startsWith('/api/connections/c1/rows?'));
    expect(asked.map((c) => new URL(c.url, 'http://test').searchParams.get('after'))).toEqual([
      null,
      '2',
      '4',
    ]);
  });

  it('starts at the first page again after a save, and loads no more while a change is reviewed', async () => {
    const user = userEvent.setup();
    const { fetcher } = fileServer();
    showFiles(fetcher);
    await screen.findByTestId('data-editor-grid');
    await user.click(screen.getByRole('button', { name: 'Load 100 more rows' }));
    await user.type(await screen.findByRole('textbox', { name: 'Row 3, code' }), '!');
    await user.click(screen.getByRole('button', { name: 'Preview' }));
    await screen.findByRole('button', { name: 'Confirm' });
    expect(screen.getByRole('button', { name: 'Load 100 more rows' })).toBeDisabled();
    await user.type(screen.getByLabelText('Type w to confirm'), 'w');
    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    await user.click(await screen.findByTestId('data-editor-execute'));
    await screen.findByTestId('data-editor-saved');
    await waitFor(() =>
      expect(screen.getByTestId('data-editor-loaded')).toHaveTextContent('2 rows are loaded'),
    );
    expect(screen.queryByRole('textbox', { name: 'Row 3, code' })).toBeNull();
  });
});
