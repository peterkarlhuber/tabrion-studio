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

function server(options: { unlocked?: boolean; execute?: () => Response } = {}) {
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
        triggers: [],
        rules: [],
        row_security: false,
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
        targets: [{ schema: 'main', table: 'parts', triggers: [], rules: [] }],
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
    expect(screen.getByTestId('data-editor-changes')).toHaveTextContent('Insert a new row');
    expect(screen.getByTestId('data-editor-changes')).toHaveTextContent('Delete the row 1');
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
