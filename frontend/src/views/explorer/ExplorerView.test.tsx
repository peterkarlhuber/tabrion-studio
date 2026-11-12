/**
 * STU-010: the explorer page. "Open" opens the profile's read-only connection (D-02 first for PROD) and reads the
 * catalog; the tree is a WAI-ARIA tree with the keyboard of the tree pattern; the filter finds objects by name prefix
 * and type; the detail tabs show columns, constraints, indexes, relationships and the SQLite facts. Stale answers are
 * dropped after a profile switch, a refresh or a new selection; the focus never falls back to the document; every
 * database text is escaped and capped (U-1); the parsers fail closed; the page sends no write but the connection open.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {
  explorerAnswer,
  HOSTILE_TABLE,
  PG_CATALOG,
  PG_TABLES,
  SQLITE_CATALOG,
  SQLITE_INFO,
  SQLITE_TABLES,
} from '../../harness/explorer';
import {
  ConnectionSessionContext,
  NO_CONNECTION_SESSION,
  prodKey,
  type ActiveConnection,
  type ConnectionSession,
} from '../../workspace';
import { ExplorerView } from './ExplorerView';
import { MAX_GROUP_ITEMS } from './tree';

const BIDI = String.fromCodePoint(0x202e);

const PROFILES = {
  profiles: [
    {
      id: 'project:parts-dev',
      source: 'project',
      name: 'parts-dev',
      engine: 'sqlite',
      environment: 'dev',
      path: 'data/parts.db',
      password: null,
      secret: { source: null, has_secret: null },
    },
    {
      id: 'user:measure',
      source: 'user',
      name: `measure${BIDI}`,
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

type Call = { url: string; method: string; headers: Record<string, string>; body?: string };
type Answer = (call: Call) => Response | Promise<Response>;

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const problem = (code: string, status: number) => json({ code, kind: 'x', status }, status);

/** A fake server: `answers` by "METHOD url" first (a list answers in turn), then the harness's explorer. */
function server(answers: Record<string, Answer | Answer[]> = {}) {
  const calls: Call[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = {
      url: String(input),
      method: init?.method ?? 'GET',
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: typeof init?.body === 'string' ? init.body : undefined,
    };
    calls.push(call);
    const answer = answers[`${call.method} ${call.url}`];
    if (Array.isArray(answer)) {
      const next = answer.length > 1 ? answer.shift() : answer[0];
      if (next) return next(call);
    } else if (answer) return answer(call);
    if (call.url === '/api/profiles') return json(PROFILES);
    return explorerAnswer(call.url, call.method, call.body) ?? new Response(null, { status: 404 });
  });
  return { fetcher: fetcher as unknown as typeof fetch, calls };
}

/** A response the test sends when it wants to. */
function later() {
  let send: (response: Response) => void = () => undefined;
  const promise = new Promise<Response>((resolve) => {
    send = resolve;
  });
  return { answer: () => promise, send };
}

const SQLITE = '/api/connections/c-sqlite';
const PG = '/api/connections/c-pg';
const table = (base: string, schema: string, name: string) =>
  `GET ${base}/table?${new URLSearchParams({ schema, name }).toString()}`;

async function openSqlite(fetcher: typeof fetch) {
  const user = userEvent.setup();
  render(<ExplorerView fetcher={fetcher} />);
  await screen.findByRole('option', { name: 'parts-dev (DEV)' });
  await user.click(screen.getByTestId('explorer-open'));
  await screen.findByRole('tree');
  return user;
}

async function openPg(fetcher: typeof fetch) {
  const user = userEvent.setup();
  render(<ExplorerView fetcher={fetcher} />);
  await screen.findByRole('option', { name: 'parts-dev (DEV)' });
  await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
  await user.click(screen.getByTestId('explorer-open'));
  const dialog = await screen.findByRole('dialog');
  await user.click(within(dialog).getByRole('button', { name: 'Connect' }));
  await screen.findByRole('tree');
  return user;
}

const rows = () => screen.getAllByRole('treeitem');
const row = (name: string) => screen.getByRole('treeitem', { name });

describe('opening and the tree', () => {
  it('opens the SQLite profile, reads the catalog and the file facts, and shows them as a tree', async () => {
    const { fetcher, calls } = server();
    await openSqlite(fetcher);
    // Open reads the profile list again before it decides on D-02 (stale-list variant of TD-038).
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      'GET /api/profiles',
      'GET /api/profiles',
      'POST /api/connections',
      `GET ${SQLITE}/catalog`,
      `GET ${SQLITE}/sqlite-info`,
    ]);
    expect(JSON.parse(calls[2]?.body ?? '')).toEqual({ profile_id: 'project:parts-dev' });
    expect(calls[2]?.headers['Content-Type']).toBe('application/json');
    // SQLite: the database file instead of schemas, then the groups of `main` (08 section 4.3).
    expect(rows().map((r) => r.getAttribute('aria-level'))).toEqual([
      '1',
      '1',
      '2',
      '2',
      '2',
      '2',
      '2',
      '2',
      '1',
    ]);
    expect(row('Database file')).not.toHaveAttribute('aria-expanded');
    // The first level is numbered on SQLite too (M17 of stu-010-ui-review.md).
    const top = rows().filter((r) => r.getAttribute('aria-level') === '1');
    expect(
      top.map((r) => [r.getAttribute('aria-posinset'), r.getAttribute('aria-setsize')]),
    ).toEqual([
      ['1', '3'],
      ['2', '3'],
      ['3', '3'],
    ]);
    expect(row('Tables (6)')).toHaveAttribute('aria-expanded', 'true');
    expect(row('Views (1)')).toHaveAttribute('aria-expanded', 'false');
    expect(row('part')).toHaveAttribute('aria-posinset', '4');
    expect(row('part')).toHaveAttribute('aria-setsize', '6');
    expect(row('part')).toHaveAttribute('aria-selected', 'false');
    expect(row('Tables (6)')).not.toHaveAttribute('aria-selected');
    expect(screen.getByTestId('explorer-message')).toHaveTextContent('Catalog read: 7 objects.');
    expect(screen.getByTestId('explorer-built')).toHaveTextContent(/^Catalog read at .+\.$/);
    expect(screen.getByTestId('env-badge')).toHaveAttribute('data-env', 'dev');
  });

  it('a PostgreSQL catalog: schemas with their groups, an empty schema, and the extensions', async () => {
    const { fetcher } = server();
    await openPg(fetcher);
    expect(rows().map((r) => r.textContent)).toEqual([
      'audit (empty)',
      'public (6)',
      'Tables (3)',
      'device',
      'measurement',
      'series',
      'Views (1)',
      'Materialized views (1)',
      'Sequences (1)',
      'Extensions',
    ]);
    expect(row('audit (empty)')).not.toHaveAttribute('aria-expanded');
    expect(row('public (6)')).toHaveAttribute('aria-expanded', 'true');
    expect(row('Extensions')).toHaveAttribute('aria-level', '1');
    expect(screen.getByTestId('explorer-built')).toHaveTextContent(/kept by the server/);
  });

  it('D-02 comes first for a PROD profile; Cancel opens nothing, and PROD is marked once open', async () => {
    const { fetcher, calls } = server();
    const user = userEvent.setup();
    render(<ExplorerView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    // U-1: the profile name is escaped in the picker and in D-02.
    expect(screen.getByRole('option', { name: 'measure\\u202E (PROD)' })).toBeInTheDocument();
    await user.click(screen.getByTestId('explorer-open'));
    let dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('measure\\u202E');
    expect(dialog).not.toHaveTextContent(BIDI);
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    await user.click(screen.getByTestId('explorer-open'));
    dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Connect' }));
    await screen.findByRole('tree');
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
    const heading = screen.getByRole('heading', { level: 2, name: /measure/ });
    expect(within(heading).getByTestId('env-badge')).toHaveAttribute('data-env', 'prod');
    expect(heading).toHaveTextContent('measure\\u202E');
    expect(heading).not.toHaveTextContent(BIDI);
  });

  it('Open uses the profile as the server has it now: one changed to PROD asks D-02', async () => {
    const dev = PROFILES.profiles[0] as Record<string, unknown>;
    const nowProd = {
      ...PROFILES,
      profiles: [{ ...dev, environment: 'prod' }, PROFILES.profiles[1]],
    };
    const { fetcher, calls } = server({
      'GET /api/profiles': [() => json(PROFILES), () => json(nowProd)],
    });
    const user = userEvent.setup();
    render(<ExplorerView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.click(screen.getByTestId('explorer-open'));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('parts-dev');
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    // The picker shows the list as it is now.
    expect(screen.getByRole('option', { name: 'parts-dev (PROD)' })).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    expect(screen.getByTestId('explorer-open')).not.toHaveAttribute('aria-disabled');
  });

  it('Open on a profile that is gone: the picker moves to a remaining profile and takes the focus', async () => {
    const { fetcher, calls } = server({
      'GET /api/profiles': [
        () => json(PROFILES),
        () => json({ ...PROFILES, profiles: [PROFILES.profiles[1]] }),
      ],
    });
    const user = userEvent.setup();
    render(<ExplorerView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.click(screen.getByTestId('explorer-open'));
    expect(await screen.findByTestId('explorer-problem')).toHaveTextContent(
      'The profile no longer exists.',
    );
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    const picker = screen.getByLabelText<HTMLSelectElement>('Connection profile');
    // The value is one of the options (not the deleted id), and the focus is on the picker, never on the body.
    expect(picker.value).toBe('user:measure');
    expect([...picker.options].map((option) => option.value)).toEqual(['user:measure']);
    await waitFor(() => expect(picker).toHaveFocus());
    expect(screen.getByTestId('explorer-open')).not.toHaveAttribute('aria-disabled');
    // The page's own selection follows the picker: Open works on the profile shown (PROD, so D-02 asks).
    expect(screen.getByTestId('explorer-open')).toBeEnabled();
    await user.click(screen.getByTestId('explorer-open'));
    expect(await screen.findByRole('dialog')).toHaveTextContent('measure');
  });

  it('Open on the last profile, now gone: the focus goes to the problem, not the body', async () => {
    const { fetcher } = server({
      'GET /api/profiles': [() => json(PROFILES), () => json({ ...PROFILES, profiles: [] })],
    });
    const user = userEvent.setup();
    render(<ExplorerView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.click(screen.getByTestId('explorer-open'));
    const alert = await screen.findByTestId('explorer-problem');
    expect(screen.getByText(/There are no connection profiles yet/)).toBeInTheDocument();
    await waitFor(() => expect(alert).toHaveFocus());
    expect(document.activeElement).not.toBe(document.body);
  });

  it('Open when the list cannot be read again: fail closed, no D-02 and no connection', async () => {
    const { fetcher, calls } = server({
      'GET /api/profiles': [() => json(PROFILES), () => new Response(null, { status: 503 })],
    });
    const user = userEvent.setup();
    render(<ExplorerView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    await user.click(screen.getByTestId('explorer-open'));
    expect(await screen.findByTestId('explorer-problem')).toHaveTextContent(
      'The local server does not answer.',
    );
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    // The same for a DEV profile: without the current list nothing is opened.
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'project:parts-dev');
    await user.click(screen.getByTestId('explorer-open'));
    await screen.findByTestId('explorer-problem');
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    expect(screen.getByTestId('explorer-open')).not.toHaveAttribute('aria-disabled');
  });

  it('says so when there are no profiles', async () => {
    const { fetcher } = server({
      'GET /api/profiles': () => json({ ...PROFILES, profiles: [] }),
    });
    render(<ExplorerView fetcher={fetcher} />);
    expect(await screen.findByText(/There are no connection profiles yet/)).toBeInTheDocument();
  });

  it('shows the problem when the connection cannot be opened', async () => {
    const { fetcher } = server({
      'POST /api/connections': () => problem('studio.database_missing', 404),
    });
    const user = userEvent.setup();
    render(<ExplorerView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.click(screen.getByTestId('explorer-open'));
    expect(await screen.findByTestId('explorer-problem')).toHaveTextContent(
      /SQLite file does not exist|database file/i,
    );
    expect(screen.queryByRole('tree')).toBeNull();
  });

  it('an empty database says so', async () => {
    const { fetcher } = server({
      [`GET ${SQLITE}/catalog`]: () => json({ ...SQLITE_CATALOG, objects: [] }),
    });
    await openSqlite(fetcher);
    expect(screen.getByTestId('explorer-empty')).toHaveTextContent(
      'This database has no tables, views or sequences.',
    );
    expect(rows().map((r) => r.textContent)).toEqual(['Database file']);
  });
});

describe('the tree keyboard (WAI-ARIA tree pattern)', () => {
  it('moves, opens, closes, goes to the parent, jumps and selects; one tab stop', async () => {
    const { fetcher, calls } = server();
    const user = await openSqlite(fetcher);
    const tabStops = () => rows().filter((r) => r.tabIndex === 0);
    expect(tabStops()).toEqual([row('Database file')]);
    row('Database file').focus();
    await user.keyboard('{ArrowDown}');
    expect(row('Tables (6)')).toHaveFocus();
    expect(tabStops()).toEqual([row('Tables (6)')]);
    await user.keyboard('{ArrowRight}'); // open: to the first child
    expect(row('alembic_version')).toHaveFocus();
    await user.keyboard('{ArrowLeft}'); // a child: to its parent
    expect(row('Tables (6)')).toHaveFocus();
    await user.keyboard('{ArrowLeft}'); // open: close it
    expect(row('Tables (6)')).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('treeitem', { name: 'part' })).toBeNull();
    await user.keyboard('{ArrowRight}'); // closed: open it, focus stays
    expect(row('Tables (6)')).toHaveAttribute('aria-expanded', 'true');
    expect(row('Tables (6)')).toHaveFocus();
    await user.keyboard('{End}');
    expect(row('Views (1)')).toHaveFocus();
    await user.keyboard('{Enter}'); // a group: Enter opens it
    expect(row('Views (1)')).toHaveAttribute('aria-expanded', 'true');
    await user.keyboard('{Home}');
    expect(row('Database file')).toHaveFocus();
    await user.keyboard('{ArrowUp}'); // nothing above the first row
    expect(row('Database file')).toHaveFocus();
    await user.keyboard('p'); // type-ahead: the next row that starts with "p"
    expect(row('part')).toHaveFocus();
    await user.keyboard('p');
    expect(row('part_relation')).toHaveFocus();
    // Moving does not select: no detail request yet.
    expect(calls.some((c) => c.url.includes('/table?'))).toBe(false);
    await user.keyboard(' ');
    expect(row('part_relation')).toHaveAttribute('aria-selected', 'true');
    expect(calls.filter((c) => c.url.includes('/table?'))).toHaveLength(1);
    await user.keyboard('{ArrowUp}{Enter}');
    expect(row('part')).toHaveAttribute('aria-selected', 'true');
    expect(row('part_relation')).toHaveAttribute('aria-selected', 'false');
    await screen.findByTestId('detail-panel');
    expect(row('part')).toHaveFocus();
  });
});

describe('the filter', () => {
  it('finds objects by name prefix and type, with their groups open, and counts them', async () => {
    const { fetcher } = server();
    const user = await openSqlite(fetcher);
    const matches = screen.getByTestId('explorer-matches');
    expect(matches).toHaveAttribute('role', 'status');
    expect(matches).toHaveTextContent('');
    await user.type(screen.getByLabelText('Filter objects'), 'PAR');
    expect(rows().map((r) => r.textContent)).toEqual([
      'Tables (2)',
      'part',
      'part_relation',
      'Views (1)',
      'part_overview',
    ]);
    expect(row('Views (1)')).toHaveAttribute('aria-expanded', 'true');
    expect(matches).toHaveTextContent('3 of 7 objects match.');
    await user.selectOptions(screen.getByLabelText('Object type'), 'view');
    expect(rows().map((r) => r.textContent)).toEqual(['Views (1)', 'part_overview']);
    expect(matches).toHaveTextContent('1 of 7 objects matches.');
    await user.type(screen.getByLabelText('Filter objects'), 'x');
    expect(screen.queryByRole('tree')).toBeNull();
    expect(screen.getByTestId('explorer-no-match')).toHaveTextContent(
      'No object matches the filter.',
    );
    await user.clear(screen.getByLabelText('Filter objects'));
    await user.selectOptions(screen.getByLabelText('Object type'), 'all');
    expect(row('Database file')).toBeInTheDocument();
    expect(row('Views (1)')).toHaveAttribute('aria-expanded', 'false');
  });

  it('while filtering, ← on an open group goes to its parent and leaves it open (L-3)', async () => {
    const { fetcher } = server();
    const user = await openPg(fetcher);
    await user.type(screen.getByLabelText('Filter objects'), 'dev');
    expect(rows().map((r) => r.textContent)).toEqual([
      'public (2)',
      'Tables (1)',
      'device',
      'Views (1)',
      'device_overview',
    ]);
    row('Views (1)').focus();
    await user.keyboard('{ArrowLeft}');
    expect(row('public (2)')).toHaveFocus();
    expect(row('Views (1)')).toHaveAttribute('aria-expanded', 'true');
    await user.keyboard('{Enter}'); // a forced-open schema does not close either
    expect(row('public (2)')).toHaveAttribute('aria-expanded', 'true');
    await user.click(row('Tables (1)'));
    expect(row('device')).toBeInTheDocument();
    // After the filter, the groups are as they were before it: Tables open, Views closed.
    await user.clear(screen.getByLabelText('Filter objects'));
    expect(row('Tables (3)')).toHaveAttribute('aria-expanded', 'true');
    expect(row('Views (1)')).toHaveAttribute('aria-expanded', 'false');
  });

  it('finds an object past the first 5000: the catalog is never cut (C-2)', async () => {
    const many = Array.from({ length: 6000 }, (_, index) => ({
      schema: 'public',
      name: `t${String(index).padStart(4, '0')}`,
      kind: 'table',
    }));
    const { fetcher } = server({
      [`GET ${PG}/catalog`]: () => json({ ...PG_CATALOG, objects: many }),
    });
    const user = await openPg(fetcher);
    expect(screen.getByTestId('explorer-message')).toHaveTextContent('Catalog read: 6000 objects.');
    expect(screen.getByTestId('explorer-large')).toHaveTextContent('6000 objects');
    fireEvent.change(screen.getByLabelText('Filter objects'), { target: { value: 't55' } });
    expect(screen.getByTestId('explorer-matches')).toHaveTextContent('100 of 6000 objects match.');
    fireEvent.change(screen.getByLabelText('Filter objects'), { target: { value: 't5999' } });
    expect(row('t5999')).toBeInTheDocument();
    await user.click(row('t5999'));
    expect(row('t5999')).toHaveAttribute('aria-selected', 'true');
  });

  it('a query with a dot matches schema.name', async () => {
    const { fetcher } = server();
    const user = await openPg(fetcher);
    await user.type(screen.getByLabelText('Filter objects'), 'public.meas');
    expect(rows().map((r) => r.textContent)).toEqual([
      'public (3)',
      'Tables (1)',
      'measurement',
      'Materialized views (1)',
      'measurement_daily',
      'Sequences (1)',
      'measurement_id_seq',
    ]);
  });

  it('Ctrl+P puts the focus in the filter, and on the profile picker before a catalog is open', async () => {
    const { fetcher } = server();
    const user = userEvent.setup();
    render(<ExplorerView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    const print = new KeyboardEvent('keydown', { key: 'p', ctrlKey: true, cancelable: true });
    act(() => {
      window.dispatchEvent(print);
    });
    expect(print.defaultPrevented).toBe(true);
    expect(screen.getByLabelText('Connection profile')).toHaveFocus();
    await user.click(screen.getByTestId('explorer-open'));
    await screen.findByRole('tree');
    await user.keyboard('{Control>}p{/Control}');
    expect(screen.getByLabelText('Filter objects')).toHaveFocus();
  });

  it('Ctrl+P does nothing while a dialog is open (M21)', async () => {
    const { fetcher } = server();
    const user = userEvent.setup();
    render(<ExplorerView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    await user.click(screen.getByTestId('explorer-open'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    const before = document.activeElement;
    const print = new KeyboardEvent('keydown', { key: 'p', ctrlKey: true, cancelable: true });
    act(() => {
      window.dispatchEvent(print);
    });
    expect(print.defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(before);
    expect(screen.getByLabelText('Connection profile')).not.toHaveFocus();
  });
});

describe('the details', () => {
  it('columns, constraints, indexes, relationships and the SQLite facts of a table', async () => {
    const { fetcher, calls } = server();
    const user = await openSqlite(fetcher);
    await user.click(row('part'));
    expect(calls.at(-1)?.url).toBe(`${SQLITE}/table?schema=main&name=part`);
    const detail = screen.getByTestId('explorer-detail');
    expect(within(detail).getByRole('heading', { level: 2 })).toHaveTextContent('main.part');
    expect(detail).toHaveTextContent('Table');
    const tabs = within(detail).getAllByRole('tab');
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      'Columns',
      'Constraints',
      'Indexes',
      'Relationships',
      'SQLite',
    ]);
    const columns = await within(detail).findAllByTestId('detail-column');
    expect(columns).toHaveLength(8);
    expect(columns[0]).toHaveTextContent('1idPKPrimary keyINTEGERNo');
    expect(columns[3]).toHaveTextContent('material_idFKForeign key');
    expect(columns[4]).toHaveTextContent("'draft'");
    // Keyboard between the tabs: ← → Home End, automatic activation.
    tabs[0]?.focus();
    await user.keyboard('{ArrowRight}');
    expect(tabs[1]).toHaveFocus();
    expect(tabs[1]).toHaveAttribute('aria-selected', 'true');
    expect(within(detail).getByTestId('detail-pk')).toHaveTextContent('pk_part: id');
    expect(within(detail).getByTestId('detail-fk')).toHaveTextContent(
      'fk_part_material: material_id → main.material (id)',
    );
    expect(within(detail).getByTestId('detail-unique')).toHaveTextContent(
      'uq_part_part_number: part_number',
    );
    expect(within(detail).getAllByTestId('detail-check')).toHaveLength(2);
    await user.keyboard('{ArrowRight}');
    expect(within(detail).getByTestId('detail-index')).toHaveTextContent(
      'ix_part_material_idmaterial_idNo',
    );
    await user.keyboard('{ArrowRight}');
    expect(within(detail).getByTestId('detail-references')).toHaveTextContent(
      'material_id → main.material (id)',
    );
    expect(
      within(detail)
        .getAllByTestId('detail-referenced-by')
        .map((i) => i.textContent),
    ).toEqual(['main.part_relation (assembly_id) → id', 'main.property (part_id) → id']);
    await user.keyboard('{End}');
    expect(tabs[4]).toHaveFocus();
    expect(within(detail).getByTestId('detail-sqlite')).toHaveTextContent(
      'STRICTYesWITHOUT ROWIDNo',
    );
    await user.keyboard('{Home}');
    expect(tabs[0]).toHaveFocus();
    await user.keyboard('{ArrowLeft}'); // wraps around
    expect(tabs[4]).toHaveFocus();
    // The panel is labelled by its tab and takes the focus (WAI-ARIA tabs).
    const panel = within(detail).getByRole('tabpanel');
    expect(panel).toHaveAttribute('aria-labelledby', tabs[4]?.id);
    expect(panel).toHaveAttribute('tabindex', '0');
  });

  it('the SQLite file facts and the PostgreSQL extensions', async () => {
    const sqlite = server();
    const user = await openSqlite(sqlite.fetcher);
    await user.click(row('Database file'));
    const file = await screen.findByTestId('detail-file');
    expect(file).toHaveTextContent('journal_modedelete');
    expect(file).toHaveTextContent('page_size4,096');
    expect(file).toHaveTextContent('foreign_keysOff');
    expect(file).toHaveTextContent('File size53,248 bytes');
    expect(file).toHaveTextContent('File permissions0600');
    expect(sqlite.calls.filter((c) => c.url.endsWith('/sqlite-info'))).toHaveLength(2);
  });

  it('PostgreSQL: extensions with their updates, the row estimate and a sequence', async () => {
    const { fetcher } = server();
    const user = await openPg(fetcher);
    await user.click(row('Extensions'));
    const items = await screen.findAllByTestId('detail-extension');
    expect(items.map((item) => item.textContent)).toEqual([
      'pg_trgm1.5publicUpdate to 1.6 available',
      'plpgsql1.0pg_catalogUp to date',
    ]);
    await user.click(row('measurement'));
    expect(await screen.findByTestId('detail-estimate')).toHaveTextContent(
      'About 4,200,000 rows (estimate of the database).',
    );
    expect(screen.queryByRole('tab', { name: 'SQLite' })).toBeNull();
    await user.click(row('Sequences (1)'));
    await user.click(row('measurement_id_seq'));
    expect(await screen.findByText(/does not show the details of a sequence yet/)).toBeVisible();
    expect(screen.queryByRole('tablist')).toBeNull();
  });

  it('following a relationship selects the target and puts the focus on its row', async () => {
    const { fetcher } = server();
    const user = await openPg(fetcher);
    await user.click(row('measurement'));
    await user.click(await screen.findByRole('tab', { name: 'Relationships' }));
    // The filter hides the target, so following it clears the filter.
    await user.type(screen.getByLabelText('Filter objects'), 'meas');
    await user.click(screen.getByRole('button', { name: 'Go to public.device' }));
    await waitFor(() => expect(row('device')).toHaveFocus());
    expect(row('device')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText('Filter objects')).toHaveValue('');
    expect(await screen.findByTestId('detail-estimate')).toHaveTextContent('About 12 rows');
    // The tab stays where it was.
    expect(screen.getByRole('tab', { name: 'Relationships' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(await screen.findByTestId('detail-referenced-by')).toHaveTextContent(
      'public.measurement (device_id) → id',
    );
  });

  it('a key to a table that is not in the catalog is text, not a button (M30)', async () => {
    const part = SQLITE_TABLES.part as Record<string, unknown>;
    const { fetcher } = server({
      [table(SQLITE, 'main', 'part')]: () =>
        json({
          ...part,
          foreign_keys: [
            {
              name: 'fk_gone',
              columns: ['material_id'],
              referred_schema: 'main',
              referred_table: 'gone',
              referred_columns: ['id'],
            },
          ],
        }),
    });
    const user = await openSqlite(fetcher);
    await user.click(row('part'));
    await user.click(await screen.findByRole('tab', { name: 'Constraints' }));
    expect(screen.getByTestId('detail-fk')).toHaveTextContent(
      'fk_gone: material_id → main.gone (id)',
    );
    expect(screen.queryByRole('button', { name: /Go to main\.gone/ })).toBeNull();
    expect(within(screen.getByTestId('detail-fk')).queryByRole('button')).toBeNull();
  });

  it('an object gone from the database: the problem in the details', async () => {
    const { fetcher } = server({
      [table(SQLITE, 'main', 'part')]: () => problem('studio.object_not_found', 404),
    });
    const user = await openSqlite(fetcher);
    await user.click(row('part'));
    expect(await screen.findByTestId('detail-problem')).toHaveTextContent(
      "The object is no longer in the database's catalog. Refresh the catalog.",
    );
  });
});

describe('U-1: database text is escaped and capped', () => {
  it('a hostile table name, column, default, comment and check text', async () => {
    const { fetcher } = server();
    const user = await openSqlite(fetcher);
    const name = 'notes\\u202Etxt.exe\\u000Ax';
    const hostile = row(name);
    await user.click(hostile);
    const detail = screen.getByTestId('explorer-detail');
    await within(detail).findAllByTestId('detail-column');
    expect(within(detail).getByRole('heading', { level: 2 })).toHaveTextContent(`main.${name}`);
    const column = within(detail).getByTestId('detail-column');
    expect(column).toHaveTextContent('body\\u202Eevil');
    // The default is cut at 160 escaped characters with a visible "…"; the comment is text, not markup.
    expect(column.textContent).toContain(`'${'x'.repeat(159)}…`);
    expect(column).toHaveTextContent('<img src=x onerror=alert(1)>');
    expect(detail.querySelector('img')).toBeNull();
    await user.click(within(detail).getByRole('tab', { name: 'Constraints' }));
    expect(within(detail).getByTestId('detail-check')).toHaveTextContent(
      'length(body) < 10\\u2066',
    );
    expect(within(detail).getByText('This object has no primary key.')).toBeInTheDocument();
    for (const raw of [BIDI, '\n', '⁦']) {
      expect(document.body.textContent).not.toContain(raw);
    }
    expect(SQLITE_TABLES[HOSTILE_TABLE]).toBeDefined();
  });

  it('a long name is cut with a marker in the tree; the escaped name is its title', async () => {
    const long = `t_${'n'.repeat(100)}`;
    const { fetcher } = server({
      [`GET ${SQLITE}/catalog`]: () =>
        json({ ...SQLITE_CATALOG, objects: [{ schema: 'main', name: long, kind: 'table' }] }),
    });
    await openSqlite(fetcher);
    const item = screen.getAllByRole('treeitem')[2] as HTMLElement;
    expect(item.textContent).toBe(`${long.slice(0, 64)}…`);
  });
});

describe('stale answers are dropped', () => {
  it('a catalog that answers after a profile switch is not shown', async () => {
    const catalog = later();
    const { fetcher } = server({ [`GET ${SQLITE}/catalog`]: catalog.answer });
    const user = userEvent.setup();
    render(<ExplorerView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.click(screen.getByTestId('explorer-open'));
    await waitFor(() =>
      expect(screen.getByTestId('explorer-message')).toHaveTextContent('Reading the catalog…'),
    );
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    await act(async () => {
      catalog.send(json(SQLITE_CATALOG));
      await Promise.resolve();
    });
    expect(screen.queryByRole('tree')).toBeNull();
    expect(screen.getByTestId('explorer-message')).toHaveTextContent('');
    // The Open button works again for the new profile (D-02 first).
    await user.click(screen.getByTestId('explorer-open'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('a PostgreSQL catalog (no file facts after it) that answers after a switch is not shown', async () => {
    const catalog = later();
    const { fetcher } = server({ [`GET ${PG}/catalog`]: catalog.answer });
    const user = userEvent.setup();
    render(<ExplorerView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    await user.click(screen.getByTestId('explorer-open'));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Connect' }));
    await waitFor(() =>
      expect(screen.getByTestId('explorer-message')).toHaveTextContent('Reading the catalog…'),
    );
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'project:parts-dev');
    await act(async () => {
      catalog.send(json(PG_CATALOG));
      await Promise.resolve();
    });
    // No PROD catalog under the DEV profile, and no PROD badge.
    expect(screen.queryByRole('tree')).toBeNull();
    expect(screen.queryByTestId('env-badge')).toBeNull();
  });

  it('switch while sqlite-info is in flight: the old catalog is not shown (Q1, M02)', async () => {
    const info = later();
    const { fetcher } = server({ [`GET ${SQLITE}/sqlite-info`]: info.answer });
    const user = userEvent.setup();
    render(<ExplorerView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.click(screen.getByTestId('explorer-open'));
    await waitFor(() =>
      expect(screen.getByTestId('explorer-message')).toHaveTextContent('Reading'),
    );
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    await act(async () => {
      info.send(json({ journal_mode: 'wal', tables: [] }));
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.queryByRole('tree')).toBeNull();
    expect(screen.queryByTestId('env-badge')).toBeNull();
  });

  it('switch while the open POST is in flight: the page is not left busy (Q2, M04)', async () => {
    const post = later();
    const { fetcher } = server({
      ['POST /api/connections']: [post.answer, () => json({ connection_id: 'c-pg' }, 201)],
    });
    const user = userEvent.setup();
    render(<ExplorerView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.click(screen.getByTestId('explorer-open'));
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    await act(async () => {
      post.send(json({ connection_id: 'c-sqlite' }, 201));
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.getByTestId('explorer-open')).not.toHaveAttribute('aria-disabled');
    expect(screen.getByTestId('explorer-message')).toHaveTextContent('');
    expect(screen.queryByRole('tree')).toBeNull();
  });

  it('late file facts of an earlier selection do not replace the shown ones (M24)', async () => {
    const facts = later();
    const { fetcher } = server({
      [`GET ${SQLITE}/sqlite-info`]: [() => json(SQLITE_INFO), facts.answer],
    });
    const user = await openSqlite(fetcher);
    await user.click(row('Database file'));
    await user.click(row('part'));
    await screen.findAllByTestId('detail-column');
    await act(async () => {
      facts.send(
        json({
          ...SQLITE_INFO,
          tables: [{ name: 'part', type: 'table', strict: false, without_rowid: true }],
        }),
      );
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    await user.click(screen.getByRole('tab', { name: 'SQLite' }));
    expect(screen.getByTestId('detail-sqlite')).toHaveTextContent('STRICTYesWITHOUT ROWIDNo');
  });

  it('a detail that answers after the next selection is not shown', async () => {
    const part = later();
    const { fetcher } = server({ [table(SQLITE, 'main', 'part')]: part.answer });
    const user = await openSqlite(fetcher);
    await user.click(row('part'));
    await user.click(row('material'));
    await screen.findAllByTestId('detail-column');
    await act(async () => {
      part.send(json(SQLITE_TABLES.part));
      await Promise.resolve();
    });
    expect(
      within(screen.getByTestId('explorer-detail')).getByRole('heading', { level: 2 }),
    ).toHaveTextContent('main.material');
    expect(screen.getAllByTestId('detail-column')).toHaveLength(4);
  });

  it('a detail that answers after a profile switch is not shown', async () => {
    const part = later();
    const { fetcher } = server({ [table(SQLITE, 'main', 'part')]: part.answer });
    const user = await openSqlite(fetcher);
    await user.click(row('part'));
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    await act(async () => {
      part.send(json(SQLITE_TABLES.part));
      await Promise.resolve();
    });
    expect(screen.queryByTestId('explorer-detail')).toBeNull();
  });

  it('a refresh overtaken by a profile switch is dropped', async () => {
    const refresh = later();
    const { fetcher } = server({
      [`GET ${SQLITE}/catalog?refresh=true`]: refresh.answer,
    });
    const user = await openSqlite(fetcher);
    await user.click(screen.getByTestId('explorer-refresh'));
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    await act(async () => {
      refresh.send(json(SQLITE_CATALOG));
      await Promise.resolve();
    });
    expect(screen.queryByRole('tree')).toBeNull();
  });

  it('a refresh keeps the selection of the moment it answers, not of the moment it started', async () => {
    const refresh = later();
    const { fetcher, calls } = server({
      [`GET ${SQLITE}/catalog?refresh=true`]: refresh.answer,
    });
    const user = await openSqlite(fetcher);
    await user.click(row('part'));
    await user.click(screen.getByTestId('explorer-refresh'));
    await user.click(row('material'));
    await screen.findAllByTestId('detail-column');
    await act(async () => {
      refresh.send(json(SQLITE_CATALOG));
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(calls.filter((c) => c.url.includes('name=material'))).toHaveLength(2),
    );
    expect(row('material')).toHaveAttribute('aria-selected', 'true');
  });
});

describe('refresh', () => {
  it('reads the catalog again on the open connection, never opens one, and re-reads the selection', async () => {
    const { fetcher, calls } = server();
    const user = await openSqlite(fetcher);
    await user.click(row('part'));
    await screen.findAllByTestId('detail-column');
    const refresh = screen.getByTestId('explorer-refresh');
    refresh.focus();
    await user.keyboard('{Enter}');
    expect(refresh).toHaveFocus();
    await waitFor(() =>
      expect(calls.filter((c) => c.url === `${SQLITE}/catalog?refresh=true`)).toHaveLength(1),
    );
    await waitFor(() => expect(calls.filter((c) => c.url.includes('name=part'))).toHaveLength(2));
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
    expect(refresh).toHaveFocus();
  });

  it('says so when the selected object is gone after a refresh', async () => {
    const { fetcher } = server({
      [`GET ${SQLITE}/catalog?refresh=true`]: () =>
        json({
          ...SQLITE_CATALOG,
          objects: SQLITE_CATALOG.objects.filter((item) => item.name !== 'part'),
        }),
    });
    const user = await openSqlite(fetcher);
    await user.click(row('part'));
    await screen.findAllByTestId('detail-column');
    await user.click(screen.getByTestId('explorer-refresh'));
    await waitFor(() =>
      expect(screen.getByTestId('explorer-message')).toHaveTextContent(
        'Catalog read: 6 objects. The selected object is no longer in the catalog.',
      ),
    );
    expect(screen.getByText('Select an object in the tree to see its details.')).toBeVisible();
  });

  it('a connection that is gone: the page forgets it, and the next PROD open asks D-02 again', async () => {
    const { fetcher, calls } = server({
      [`GET ${PG}/catalog?refresh=true`]: () => problem('studio.connection_not_found', 404),
    });
    const user = await openPg(fetcher);
    await user.click(screen.getByTestId('explorer-refresh')); // the click puts the focus on Refresh
    expect(await screen.findByTestId('explorer-problem')).toHaveTextContent(
      'The connection is no longer open.',
    );
    expect(screen.queryByRole('tree')).toBeNull();
    // The focused Refresh button is gone with the workspace; the focus moves to Open, never to the body (C-4).
    expect(screen.getByTestId('explorer-open')).toHaveFocus();
    expect(document.activeElement).not.toBe(document.body);
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
    await user.click(screen.getByTestId('explorer-open'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('a refused refresh keeps the catalog that is shown', async () => {
    const { fetcher } = server({
      [`GET ${SQLITE}/catalog?refresh=true`]: () => problem('studio.connection_busy', 503),
    });
    const user = await openSqlite(fetcher);
    await user.click(screen.getByTestId('explorer-refresh'));
    expect(await screen.findByTestId('explorer-problem')).toHaveTextContent(
      'The connection is busy.',
    );
    expect(screen.getByRole('tree')).toBeInTheDocument();
  });
});

describe('focus and the status region', () => {
  it('the status region is in the page before its first message, hidden only visually', async () => {
    const { fetcher } = server();
    render(<ExplorerView fetcher={fetcher} />);
    const status = screen.getByTestId('explorer-message');
    expect(status).toHaveAttribute('role', 'status');
    expect(status).toHaveClass('tb-sr-only');
    expect(status).toBeEmptyDOMElement();
  });

  it('Open keeps the focus while it works and after; a second press while busy sends nothing', async () => {
    const catalog = later();
    const { fetcher, calls } = server({ [`GET ${SQLITE}/catalog`]: catalog.answer });
    const user = userEvent.setup();
    render(<ExplorerView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    const open = screen.getByTestId('explorer-open');
    open.focus();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(open).toHaveAttribute('aria-disabled', 'true'));
    expect(open).not.toBeDisabled();
    expect(open).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
    await act(async () => {
      catalog.send(json(SQLITE_CATALOG));
      await Promise.resolve();
    });
    await screen.findByRole('tree');
    expect(open).not.toHaveAttribute('aria-disabled');
    expect(open).toHaveFocus();
  });
});

describe('fail closed', () => {
  it('a catalog without its object list is refused, not shown as empty', async () => {
    const { fetcher } = server({
      [`GET ${SQLITE}/catalog`]: () => json({ engine: 'sqlite', schemas: ['main'] }),
    });
    const user = userEvent.setup();
    render(<ExplorerView fetcher={fetcher} />);
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.click(screen.getByTestId('explorer-open'));
    expect(await screen.findByTestId('explorer-problem')).toHaveTextContent(
      "The server's answer could not be read, so nothing of it is shown.",
    );
    expect(screen.queryByRole('tree')).toBeNull();
  });

  it('an unknown kind is listed under "Other objects"; a time out of range shows no time', async () => {
    const { fetcher } = server({
      [`GET ${SQLITE}/catalog`]: () =>
        json({
          ...SQLITE_CATALOG,
          built_at: 1e15,
          objects: [...SQLITE_CATALOG.objects, { schema: 'main', name: 'idx', kind: 'index' }],
        }),
    });
    const user = await openSqlite(fetcher);
    expect(screen.queryByTestId('explorer-built')).toBeNull();
    expect(row('Other objects (1)')).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Object type'), 'other');
    expect(rows().map((r) => r.textContent)).toEqual(['Other objects (1)', 'idx']);
  });

  it('numbers that are not counts are not shown', async () => {
    const { fetcher } = server({
      [table(PG, 'public', 'measurement')]: () =>
        json({ ...PG_TABLES.measurement, row_estimate: -1 }),
      [table(PG, 'public', 'device')]: () => json({ ...PG_TABLES.device, row_estimate: 1.5 }),
    });
    const user = await openPg(fetcher);
    await user.click(row('measurement'));
    await screen.findAllByTestId('detail-column');
    expect(screen.queryByTestId('detail-estimate')).toBeNull();
    await user.click(row('device'));
    await screen.findAllByTestId('detail-column');
    expect(screen.queryByTestId('detail-estimate')).toBeNull();
  });

  it('a group with more objects than it renders says so; a large catalog says it loads names only', async () => {
    const many = Array.from({ length: 2100 }, (_, index) => ({
      schema: 'public',
      name: `t${String(index).padStart(4, '0')}`,
      kind: 'table',
    }));
    const { fetcher } = server({
      [`GET ${PG}/catalog`]: () => json({ ...PG_CATALOG, objects: many }),
    });
    await openPg(fetcher);
    expect(screen.getAllByRole('treeitem', { name: /^t\d{4}$/ })).toHaveLength(MAX_GROUP_ITEMS);
    expect(screen.getByTestId('explorer-truncated')).toBeInTheDocument();
    expect(screen.getByTestId('explorer-large')).toHaveTextContent('2100 objects');
  });
});

describe('no write affordance (STU-026 stays locked)', () => {
  it('the only request that is not a GET is the connection open; no button offers a change', async () => {
    const { fetcher, calls } = server();
    const user = await openPg(fetcher);
    await user.click(row('measurement'));
    await screen.findAllByTestId('detail-column');
    for (const tab of screen.getAllByRole('tab')) fireEvent.click(tab);
    await user.click(row('Extensions'));
    await screen.findAllByTestId('detail-extension');
    expect(calls.filter((c) => c.method !== 'GET').map((c) => `${c.method} ${c.url}`)).toEqual([
      'POST /api/connections',
    ]);
    const buttons = screen.getAllByRole('button').map((b) => b.textContent ?? '');
    for (const word of [/edit/i, /delete/i, /drop/i, /insert/i, /update(?! to)/i, /save/i]) {
      expect(buttons.filter((label) => word.test(label))).toEqual([]);
    }
  });
});

describe('the shared connection state of STU-008 (#42)', () => {
  /** A page session as App keeps it: the confirmed PROD targets and the header's connection. */
  function session() {
    const confirmed = new Set<string>();
    const state: { active: ActiveConnection | null } = { active: null };
    let connects = 0;
    const value: ConnectionSession = {
      ...NO_CONNECTION_SESSION,
      prodConfirmed: (target) => confirmed.has(prodKey(target)),
      confirmProd: (target) => {
        confirmed.add(prodKey(target));
      },
      setActive: (next) => {
        state.active = typeof next === 'function' ? next(state.active) : next;
      },
      beginConnect: () => ++connects,
      isLatestConnect: (request) => request === connects,
      headerVersion: () => connects,
    };
    return { value, state, confirmed };
  }

  it('D-02 once per page session; the opened connection becomes the header connection', async () => {
    const { fetcher, calls } = server();
    const shared = session();
    const user = userEvent.setup();
    render(
      <ConnectionSessionContext.Provider value={shared.value}>
        <ExplorerView fetcher={fetcher} />
      </ConnectionSessionContext.Provider>,
    );
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    await user.click(screen.getByTestId('explorer-open'));
    const dialog = screen.getByRole('dialog');
    // D-02 names the profile's statement timeout (15 s for PROD by default).
    expect(dialog).toHaveTextContent('15');
    await user.click(within(dialog).getByRole('button', { name: 'Connect' }));
    await screen.findByRole('tree');
    expect(shared.confirmed.size).toBe(1);
    expect(shared.state.active).toMatchObject({
      environment: 'prod',
      status: 'connected',
      connectionId: 'c-pg',
      profileId: 'user:measure',
    });
    // Confirmed in this page session: a second open does not ask again.
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'project:parts-dev');
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    await user.click(screen.getByTestId('explorer-open'));
    expect(screen.queryByRole('dialog')).toBeNull();
    await screen.findByRole('tree');
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(2);
  });

  it('a PROD profile whose target changed while the page stayed open asks D-02 again', async () => {
    const measure = PROFILES.profiles[1] as Record<string, unknown>;
    const moved = {
      ...PROFILES,
      profiles: [PROFILES.profiles[0], { ...measure, host: 'db2.internal' }],
    };
    const { fetcher, calls } = server({
      'GET /api/profiles': [() => json(PROFILES), () => json(PROFILES), () => json(moved)],
    });
    const shared = session();
    const user = userEvent.setup();
    render(
      <ConnectionSessionContext.Provider value={shared.value}>
        <ExplorerView fetcher={fetcher} />
      </ConnectionSessionContext.Provider>,
    );
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    await user.click(screen.getByTestId('explorer-open'));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Connect' }),
    );
    await screen.findByRole('tree');
    // Same profile id, other host: the confirmation was for the old target, so D-02 asks again.
    await user.click(screen.getByTestId('explorer-open'));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Connect' }));
    await screen.findByRole('tree');
    expect(shared.confirmed.size).toBe(2);
  });

  it('a connection gone on Refresh leaves the header too', async () => {
    const { fetcher } = server({
      [`GET ${PG}/catalog?refresh=true`]: () => problem('studio.connection_not_found', 404),
    });
    const shared = session();
    const user = userEvent.setup();
    render(
      <ConnectionSessionContext.Provider value={shared.value}>
        <ExplorerView fetcher={fetcher} />
      </ConnectionSessionContext.Provider>,
    );
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'user:measure');
    await user.click(screen.getByTestId('explorer-open'));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Connect' }));
    await screen.findByRole('tree');
    expect(shared.state.active?.connectionId).toBe('c-pg');
    await user.click(screen.getByTestId('explorer-refresh'));
    await screen.findByTestId('explorer-problem');
    expect(shared.state.active).toBeNull();
  });

  it('an open overtaken by a newer connect elsewhere does not set the header', async () => {
    const post = later();
    const { fetcher } = server({ ['POST /api/connections']: post.answer });
    const shared = session();
    const user = userEvent.setup();
    render(
      <ConnectionSessionContext.Provider value={shared.value}>
        <ExplorerView fetcher={fetcher} />
      </ConnectionSessionContext.Provider>,
    );
    await screen.findByRole('option', { name: 'parts-dev (DEV)' });
    await user.click(screen.getByTestId('explorer-open'));
    shared.value.beginConnect(); // the connection manager connects meanwhile
    await act(async () => {
      post.send(json({ connection_id: 'c-sqlite' }, 201));
      await Promise.resolve();
    });
    await screen.findByRole('tree');
    expect(shared.state.active).toBeNull();
  });
});
