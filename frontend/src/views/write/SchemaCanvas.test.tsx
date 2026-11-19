/**
 * STU-XSD-1 (TX-31, GS3-23): the Schema Designer's graphical mode. The diagram draws the draft the server answers and
 * has no route of its own: a table chosen there shows its card from the table view, whose forms send the same edit
 * to the same route as in the table view, and a line drawn from a column to a column only fills in the table's "New
 * constraint" form. The table view stays the accessible alternative; axe finds no serious or critical violation in
 * either view (jsdom: no colour contrast; e2e/schema-designer-backend.spec.ts has it, and draws a line).
 *
 * jsdom cannot drag xyflow's handles: the drawn line is handed to the page by a stand-in canvas (`fake.on`), the real
 * canvas is rendered in every other test. `schemaCanvasModel.ts` turns xyflow's connection into the key.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';

import { parseDraft } from '../../api/schemaDesigner';
import { MAX_BOX_COLUMNS, MAX_DIAGRAM_TABLES } from '../explorer/er/layout';
import type * as CanvasModule from './SchemaCanvas';
import type { SchemaCanvasProps } from './SchemaCanvas';
import { diagramTables, drawnKey, handleId } from './schemaCanvasModel';
import { SchemaDesignerSection } from './SchemaDesigner';

const fake = vi.hoisted(() => ({ on: false }));

vi.mock('./SchemaCanvas', async (importOriginal) => {
  const real = await importOriginal<typeof CanvasModule>();
  /** The stand-in: one button per table to choose it, and one that draws orders.part_id -> parts.id. */
  function Stand(props: SchemaCanvasProps) {
    return (
      <div data-testid="stand-in-canvas">
        {props.draft.tables.map((table) => (
          <button key={table.name} type="button" onClick={() => props.onChoose(table.name)}>
            {`choose ${table.name}`}
          </button>
        ))}
        <button
          type="button"
          onClick={() =>
            props.onDraw({
              table: 'orders',
              column: 'part_id',
              referredTable: 'parts',
              referredColumn: 'id',
            })
          }
        >
          draw a line
        </button>
      </div>
    );
  }
  function Switch(props: SchemaCanvasProps) {
    return fake.on ? <Stand {...props} /> : <real.SchemaCanvas {...props} />;
  }
  return { ...real, default: Switch };
});

// xyflow measures its boxes; jsdom has no ResizeObserver.
class NoResize {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', NoResize);
});
afterAll(() => {
  vi.unstubAllGlobals();
});
afterEach(() => {
  fake.on = false;
});

const PROFILE = {
  id: 'user:parts',
  source: 'user',
  name: 'parts',
  engine: 'sqlite',
  environment: 'dev',
  path: '/data/parts.db',
};

function column(name: string, type = 'INTEGER') {
  return { name, type, nullable: true, default: null, identity: null, generated: null };
}

function table(name: string, status: string, extra: Record<string, unknown> = {}) {
  return {
    name,
    status,
    columns: [column('id')],
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

const ORDERS = table('orders', 'new', { columns: [column('id'), column('part_id')] });
const LINKED = table('orders', 'new', {
  columns: [column('id'), column('part_id')],
  foreign_keys: [
    {
      name: null,
      columns: ['part_id'],
      referred_table: 'parts',
      referred_columns: ['id'],
      on_delete: null,
      on_update: null,
    },
  ],
});

function draft(revision: number, tables: unknown[]) {
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

interface Call {
  method: string;
  url: string;
  body: Record<string, unknown> | null;
}

function setup(options: { refuse?: boolean; tables?: unknown[] } = {}) {
  const calls: Call[] = [];
  let current = draft(0, options.tables ?? [table('parts', 'unchanged'), ORDERS]);
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
    if (url === '/api/write/areas')
      return json({
        areas: { data_editor: false, schema_designer: true, database_manager: false },
      });
    if (url === '/api/profiles')
      return json({
        profiles: [PROFILE],
        project_state: 'none',
        user_file: { digest: 'd'.repeat(64), hand_written: false },
        issues: {},
      });
    if (url === '/api/connections')
      return json({
        connections: [{ connection_id: 'c_1', profile_id: 'user:parts', environment: 'dev' }],
      });
    if (url === '/api/write/unlocks')
      return json({
        unlocks: [{ connection_id: 'c_1', write_profile_id: 'write:parts-w', environment: 'dev' }],
      });
    if (url === '/api/write/schema-designer/draft/view') return json(current);
    if (url === '/api/write/schema-designer/draft/edits') {
      // The server decides: here it refuses the key's column, as tabrion or the draft's checks would.
      if (options.refuse === true)
        return json(
          {
            code: 'studio.schema_edit_invalid',
            kind: 'request',
            status: 400,
            title: 'x',
            detail: 'x',
            issues: [{ pointer: 'edit.referred_columns', code: 'edit.column_missing' }],
          },
          400,
        );
      current = draft(current.revision + 1, [table('parts', 'unchanged'), LINKED]);
      return json(current);
    }
    return new Response(null, { status: 404 });
  };
  render(<SchemaDesignerSection fetcher={fetcher} />);
  return { calls };
}

async function serious(container: HTMLElement): Promise<string[]> {
  const result = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
  return result.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => v.id);
}

async function showDiagram(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByTestId('schema-designer-draft');
  const views = screen.getByRole('group', { name: 'Show the draft as' });
  await user.click(within(views).getByRole('button', { name: 'Diagram' }));
  expect(within(views).getByRole('button', { name: 'Diagram' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
}

/**
 * A box's name button: "Edit table parts" in a browser (e2e). jsdom's accessible name joins the button's spans
 * without the spaces at their ends ("Edit tableparts"), so the test allows for that.
 */
function named(rest: string): RegExp {
  const words = rest.split(' ').map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  return new RegExp(`^Edit table\\s*${words.join('\\s*')}$`);
}

/** The calls to the server that are not reads of the page's state. */
function designerWrites(calls: Call[]) {
  return calls.filter((c) => c.method !== 'GET' && !c.url.endsWith('/draft/view'));
}

describe('schemaCanvasModel (STU-XSD-1)', () => {
  const parsed = parseDraft(draft(0, [table('parts', 'unchanged'), LINKED]));
  if (parsed === null) throw new Error('fixture');

  it('gives the explorer scene the draft tables, their keys in the draft schema', () => {
    const tables = diagramTables(parsed);
    expect(tables.map((item) => item.name)).toEqual(['parts', 'orders']);
    expect(tables[1]?.foreignKeys).toEqual([
      {
        name: null,
        columns: ['part_id'],
        referredSchema: 'main',
        referredTable: 'parts',
        referredColumns: ['id'],
      },
    ]);
    const many = parseDraft(
      draft(
        0,
        Array.from({ length: MAX_DIAGRAM_TABLES + 3 }, (_, n) => table(`t${n}`, 'unchanged')),
      ),
    );
    expect(many === null ? 0 : diagramTables(many).length).toBe(MAX_DIAGRAM_TABLES);
  });

  it('reads a drawn line back as a key only from a from-handle to a to-handle of a listed column', () => {
    const line = (sourceHandle: string | null, targetHandle: string | null, target = 'parts') => ({
      source: 'orders',
      target,
      sourceHandle,
      targetHandle,
    });
    expect(drawnKey(parsed, line(handleId('from', 1), handleId('to', 0)))).toEqual({
      table: 'orders',
      column: 'part_id',
      referredTable: 'parts',
      referredColumn: 'id',
    });
    // The wrong sides, no handle, a column that is not there, a table that is not there, a hidden column.
    expect(drawnKey(parsed, line(handleId('to', 1), handleId('to', 0)))).toBeNull();
    expect(drawnKey(parsed, line(handleId('from', 1), handleId('from', 0)))).toBeNull();
    expect(drawnKey(parsed, line(null, handleId('to', 0)))).toBeNull();
    expect(drawnKey(parsed, line(handleId('from', 7), handleId('to', 0)))).toBeNull();
    expect(drawnKey(parsed, line(handleId('from', 1), handleId('to', 0), 'nope'))).toBeNull();
    expect(drawnKey(parsed, line(handleId('from', MAX_BOX_COLUMNS), handleId('to', 0)))).toBeNull();
    expect(drawnKey(parsed, line('from:1x', handleId('to', 0)))).toBeNull();
  });
});

describe('the Schema Designer diagram (STU-XSD-1)', () => {
  it('draws the draft with each table status and keeps the table view as the default', async () => {
    const user = userEvent.setup();
    setup({ tables: [table('parts', 'unchanged'), LINKED] });
    // The table view first: every table's card, no diagram.
    expect(await screen.findAllByTestId('schema-designer-table')).toHaveLength(2);
    expect(screen.queryByTestId('schema-canvas')).toBeNull();
    await showDiagram(user);
    const canvas = await screen.findByTestId('schema-canvas');
    expect(canvas).toHaveAccessibleName('Diagram of the draft, 2 tables');
    const boxes = await within(canvas).findAllByTestId('schema-canvas-box');
    expect(boxes.map((box) => box.dataset.table)).toEqual(['parts', 'orders']);
    expect(within(canvas).getByRole('button', { name: named('orders new') })).toBeVisible();
    expect(within(canvas).getByRole('button', { name: named('parts') })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    // One dot per listed column on each side.
    const orders = boxes[1] as HTMLElement;
    expect(orders.parentElement?.querySelectorAll('[data-handleid^="from:"]')).toHaveLength(2);
    expect(orders.parentElement?.querySelectorAll('[data-handleid^="to:"]')).toHaveLength(2);
    // No card until a table is chosen; the cards of the table view are gone.
    expect(screen.queryAllByTestId('schema-designer-table')).toHaveLength(0);
    expect(screen.getByTestId('schema-canvas-chosen')).toHaveTextContent('No table chosen.');
    expect(await serious(document.body)).toEqual([]);
    await user.click(screen.getByRole('button', { name: 'Table view' }));
    expect(screen.queryByTestId('schema-canvas')).toBeNull();
    expect(screen.getAllByTestId('schema-designer-table')).toHaveLength(2);
  });

  it('shows the chosen table card, which sends the same edit to the same route', async () => {
    const user = userEvent.setup();
    const { calls } = setup();
    await showDiagram(user);
    const name = await screen.findByRole('button', { name: named('parts') });
    // From the keyboard (a click without pointer detail): the card takes the focus.
    act(() => name.focus());
    fireEvent.click(name, { detail: 0 });
    const card = await screen.findByTestId('schema-canvas-card');
    expect(card).toHaveFocus();
    expect(name).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('schema-canvas-chosen')).toHaveTextContent('Editing table parts.');
    const tableCard = within(card).getByTestId('schema-designer-table');
    expect(within(tableCard).getByRole('heading', { name: 'parts' })).toBeInTheDocument();
    expect(await serious(document.body)).toEqual([]);

    await user.click(within(tableCard).getByRole('button', { name: 'Add column' }));
    const form = within(tableCard).getByTestId('schema-designer-form');
    await user.type(within(form).getByLabelText('Column name'), 'qty');
    await user.type(within(form).getByLabelText('Type'), 'INTEGER');
    await user.click(within(form).getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(screen.queryByTestId('schema-designer-form')).toBeNull());
    expect(designerWrites(calls)).toEqual([
      {
        method: 'POST',
        url: '/api/write/schema-designer/draft/edits',
        body: {
          connection_id: 'c_1',
          revision: 0,
          edit: {
            op: 'add_column',
            table: 'parts',
            column: { name: 'qty', type: 'INTEGER', nullable: true },
          },
        },
      },
    ]);
    // The page shows the server's answer and keeps the chosen table.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: named('parts') })).toHaveAttribute(
        'aria-pressed',
        'true',
      ),
    );
  });

  it('fills the New constraint form with a drawn line and sends nothing before Apply', async () => {
    fake.on = true;
    const user = userEvent.setup();
    const { calls } = setup();
    await showDiagram(user);
    await user.click(await screen.findByRole('button', { name: 'draw a line' }));
    const card = await screen.findByTestId('schema-canvas-card');
    expect(card).toHaveFocus();
    const form = within(card).getByTestId('schema-designer-form');
    expect(within(form).getByRole('heading')).toHaveTextContent('New constraint on orders');
    expect(within(form).getByLabelText('Kind')).toHaveValue('foreign_key');
    expect(within(form).getByLabelText('Columns')).toHaveValue('part_id');
    expect(within(form).getByLabelText('Referred table')).toHaveValue('parts');
    expect(within(form).getByLabelText('Referred columns')).toHaveValue('id');
    expect(designerWrites(calls)).toEqual([]);
    expect(await serious(document.body)).toEqual([]);

    await user.click(within(form).getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(screen.queryByTestId('schema-designer-form')).toBeNull());
    expect(designerWrites(calls)).toEqual([
      {
        method: 'POST',
        url: '/api/write/schema-designer/draft/edits',
        body: {
          connection_id: 'c_1',
          revision: 0,
          edit: {
            op: 'add_constraint',
            table: 'orders',
            constraint: {
              kind: 'foreign_key',
              columns: ['part_id'],
              referred_table: 'parts',
              referred_columns: ['id'],
            },
          },
        },
      },
    ]);
  });

  it('shows the server refusal of a drawn key at its field, like the table view', async () => {
    fake.on = true;
    const user = userEvent.setup();
    const { calls } = setup({ refuse: true });
    await showDiagram(user);
    await user.click(await screen.findByRole('button', { name: 'draw a line' }));
    const form = await screen.findByTestId('schema-designer-form');
    await user.click(within(form).getByRole('button', { name: 'Apply' }));
    expect(await within(form).findByText('No such column in the table.')).toBeVisible();
    expect(screen.getByTestId('schema-designer-form')).toBeInTheDocument();
    expect(designerWrites(calls)).toHaveLength(1);
  });

  it('says when the diagram shows only the first 150 tables', async () => {
    const user = userEvent.setup();
    setup({
      tables: Array.from({ length: MAX_DIAGRAM_TABLES + 2 }, (_, n) => table(`t${n}`, 'unchanged')),
    });
    await showDiagram(user);
    expect(await screen.findByTestId('schema-canvas-capped')).toHaveTextContent(
      'The diagram shows the first 150 of 152 tables. The table view shows all of them.',
    );
  });
});
