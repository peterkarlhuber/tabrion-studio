/**
 * STU-013 page: the data viewer as the explorer's "Data" tab. First page, Next / Previous through the server's
 * tokens, sort, filters, page size and the byte bound, the in-page search, the cell detail with "Load in full", the
 * stale-answer guards (another object, a profile switch, leaving the tab), a connection that is gone, an evicted
 * page, the explorer's "SELECT … LIMIT 100 in the SQL editor", and no request but reads.
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { explorerAnswer, HOSTILE_TABLE } from '../../harness/explorer';
import { createViewer, HOSTILE_VALUE, LONG_CHARS, PAGE_CHARS } from '../../harness/viewer';
import {
  ConnectionSessionContext,
  NO_CONNECTION_SESSION,
  type ActiveConnection,
  type ConnectionSession,
} from '../../workspace';
import { takeHandoff } from '../editor/handoff';
import { ExplorerView } from '../explorer/ExplorerView';

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
      id: 'project:parts-pg',
      source: 'project',
      name: 'parts-pg',
      engine: 'postgresql',
      environment: 'test',
      host: 'db.internal',
      database: 'm',
      user: 'ro',
      sslmode: 'verify-full',
      password: null,
      secret: { source: null, has_secret: null },
    },
  ],
  project_state: 'trusted',
  issues: { user: [] },
  user_file: { digest: 'b'.repeat(64), hand_written: false },
};

type Call = { url: string; method: string };

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const problem = (code: string, status: number) => json({ code, kind: 'x', status }, status);

/** The explorer's and the viewer's fake answers; `before` answers first (null: not this one). */
function server(before: (call: Call) => Response | Promise<Response> | null = () => null) {
  const viewer = createViewer();
  const calls: Call[] = [];
  const fetcher = async (input: RequestInfo | URL, init?: RequestInit) => {
    const call = { url: String(input), method: init?.method ?? 'GET' };
    calls.push(call);
    const early = before(call);
    if (early !== null) return early;
    if (call.url === '/api/profiles') return json(PROFILES);
    if (call.method === 'GET') {
      const page = viewer.answer(call.url);
      if (page !== null) return page;
    }
    const body = typeof init?.body === 'string' ? init.body : '';
    return explorerAnswer(call.url, call.method, body) ?? new Response(null, { status: 404 });
  };
  return { fetcher: fetcher as typeof fetch, calls, log: viewer.log };
}

/** A response the test sends when it wants to. */
function later() {
  let send: (response: Response) => void = () => undefined;
  const promise = new Promise<Response>((resolve) => {
    send = resolve;
  });
  return { answer: () => promise, send };
}

function session() {
  const state: { active: ActiveConnection | null } = { active: null };
  let connects = 0;
  const value: ConnectionSession = {
    ...NO_CONNECTION_SESSION,
    setActive: (next) => {
      state.active = typeof next === 'function' ? next(state.active) : next;
    },
    beginConnect: () => ++connects,
    isLatestConnect: (request) => request === connects,
    headerVersion: () => connects,
  };
  return { value, state };
}

async function open(fetcher: typeof fetch, profile = 'project:parts-dev', shared = session()) {
  const user = userEvent.setup();
  render(
    <ConnectionSessionContext.Provider value={shared.value}>
      <ExplorerView fetcher={fetcher} />
    </ConnectionSessionContext.Provider>,
  );
  await screen.findByRole('option', { name: 'parts-dev (DEV)' });
  if (profile !== 'project:parts-dev') {
    await user.selectOptions(screen.getByLabelText('Connection profile'), profile);
  }
  await user.click(screen.getByTestId('explorer-open'));
  await screen.findByRole('tree');
  return { user, shared };
}

async function showData(
  user: ReturnType<typeof userEvent.setup>,
  name: string | RegExp,
  wait = true,
) {
  await user.click(screen.getByRole('treeitem', { name }));
  await waitFor(() => expect(screen.getByTestId('explorer-show-data')).toBeEnabled());
  await user.click(screen.getByTestId('explorer-show-data'));
  if (wait) await screen.findByTestId('viewer');
}

const status = () => screen.getByTestId('viewer-status');
const grid = () => screen.getByRole('grid');
const cellText = (row: number, column: number) =>
  grid().querySelector(`[data-row="${row}"][data-column="${column}"]`)?.textContent ?? '';

// The grid is virtualized: jsdom has no layout, so the grid's scroller gets a size (as in EditorView.test.tsx).
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('the data tab (STU-013 page)', () => {
  it('"Show data" opens the tab with the focus on it and reads the first page', async () => {
    const { fetcher, log } = server();
    const { user } = await open(fetcher);
    await showData(user, 'part');
    const tab = screen.getByRole('tab', { name: 'Data' });
    await waitFor(() => expect(tab).toHaveFocus());
    expect(tab).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–100; more rows follow.'));
    // One plain first page: no sort, no filter, no `after` token.
    expect(log.pages).toHaveLength(1);
    expect(log.pages[0]).toMatchObject({
      table: 'c-sqlite|part',
      limit: 100,
      sort: null,
      desc: false,
    });
    expect(log.pages[0]?.filter).toBeNull();
    expect(log.pages[0]?.after).toBeNull();
    expect(grid()).toHaveAttribute('aria-rowcount', '101');
    expect(grid()).toHaveAccessibleName('Data of main.part');
    await waitFor(() => expect(cellText(0, 1)).toBe('P-0001'));
    expect(screen.queryByTestId('viewer-limited')).toBeNull();
    expect(screen.queryByTestId('viewer-hint')).toBeNull();
  });

  it('Next and Previous walk the pages with the server tokens; the last page says so', async () => {
    const { fetcher, log } = server();
    const { user } = await open(fetcher);
    await showData(user, 'part');
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–100'));
    expect(screen.getByTestId('viewer-previous')).toHaveAttribute('aria-disabled', 'true');
    await user.click(screen.getByTestId('viewer-next'));
    await waitFor(() => expect(status()).toHaveTextContent('Rows 101–200; more rows follow.'));
    expect(log.pages[1]?.after).toBe('vp1');
    await waitFor(() => expect(cellText(0, 0)).toBe('101'));
    await user.click(screen.getByTestId('viewer-next'));
    await waitFor(() => expect(status()).toHaveTextContent('Rows 201–250; this is the last page.'));
    expect(screen.getByTestId('viewer-next')).toHaveAttribute('aria-disabled', 'true');
    await user.click(screen.getByTestId('viewer-previous'));
    await waitFor(() => expect(status()).toHaveTextContent('Rows 101–200'));
    expect(log.pages.at(-1)?.after).toBe('vp1');
    await user.click(screen.getByTestId('viewer-previous'));
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–100'));
    expect(log.pages.at(-1)?.after).toBeNull();
  });

  it('sort and direction start again at the first page', async () => {
    const { fetcher, log } = server();
    const { user } = await open(fetcher);
    await showData(user, 'part');
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–100'));
    await user.click(screen.getByTestId('viewer-next'));
    await waitFor(() => expect(status()).toHaveTextContent('Rows 101–200'));
    expect(screen.getByTestId('viewer-direction')).toBeDisabled();
    await user.selectOptions(screen.getByTestId('viewer-sort'), 'id');
    await user.selectOptions(screen.getByTestId('viewer-direction'), 'desc');
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–100'));
    expect(log.pages.at(-1)).toMatchObject({ sort: 'id', desc: true, after: null });
    await waitFor(() => expect(cellText(0, 0)).toBe('250'));
  });

  it('filters are sent as the server takes them, all must hold, and Clear removes them', async () => {
    const { fetcher, log } = server();
    const { user } = await open(fetcher);
    await showData(user, 'part');
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–100'));
    await user.click(screen.getByTestId('viewer-add-filter'));
    const column = screen.getByLabelText('Column of filter 1');
    await waitFor(() => expect(column).toHaveFocus());
    await user.selectOptions(column, 'status');
    await user.type(screen.getByLabelText('Value of filter 1'), 'released');
    await user.click(screen.getByTestId('viewer-add-filter'));
    await user.selectOptions(screen.getByLabelText('Column of filter 2'), 'updated_at');
    await user.selectOptions(screen.getByLabelText('Operator of filter 2'), 'is_null');
    // An operator without a value has no value field.
    expect(screen.queryByLabelText('Value of filter 2')).toBeNull();
    await user.click(screen.getByTestId('viewer-apply'));
    await waitFor(() =>
      expect(screen.getByTestId('viewer-filters-applied')).toHaveTextContent('(2 applied)'),
    );
    expect(JSON.parse(log.pages.at(-1)?.filter ?? '[]')).toEqual([
      { column: 'status', op: 'eq', value: 'released' },
      { column: 'updated_at', op: 'is_null' },
    ]);
    // Parts 9, 45, 81 ... have no updated_at; of those, id % 4 === 1 are "released": 9, 45, 81, 117, ...
    await waitFor(() => expect(status()).toHaveTextContent('this is the last page'));
    await waitFor(() => expect(cellText(0, 0)).toBe('9'));
    await user.click(screen.getByRole('button', { name: 'Remove filter 1' }));
    await user.click(screen.getByRole('button', { name: 'Remove filter 1' }));
    await user.click(screen.getByTestId('viewer-clear'));
    await waitFor(() => expect(log.pages.at(-1)?.filter).toBeNull());
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–100'));
  });

  it('a filter that matches nothing says so', async () => {
    const { fetcher } = server();
    const { user } = await open(fetcher);
    await showData(user, 'part');
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–100'));
    await user.click(screen.getByTestId('viewer-add-filter'));
    await user.type(screen.getByLabelText('Value of filter 1'), '99999');
    await user.click(screen.getByTestId('viewer-apply'));
    await waitFor(() => expect(status()).toHaveTextContent('No row matches the filters.'));
  });

  it('the page size is bounded in bytes by the column types, and the page says so', async () => {
    const { fetcher, log } = server();
    const { user } = await open(fetcher, 'project:parts-pg');
    await showData(user, 'device');
    await waitFor(() => expect(status()).toHaveTextContent('this is the last page'));
    await user.selectOptions(screen.getByTestId('viewer-page-size'), '1000');
    // device: BIGINT and TEXT; a TEXT value can be 64 K characters (128 KB): 64 MB hold 511 such rows.
    await waitFor(() => expect(log.pages.at(-1)?.limit).toBe(511));
    expect(screen.getByTestId('viewer-limited')).toHaveTextContent(
      'Pages of this table hold at most 511 rows',
    );
  });

  it('the search finds rows of the current page only, and the cell detail maps to the page row', async () => {
    const { fetcher, log } = server();
    const { user } = await open(fetcher);
    await showData(user, 'part');
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–100'));
    await user.type(screen.getByTestId('viewer-search'), 'long part');
    await waitFor(() => expect(status()).toHaveTextContent('1 of 100 rows on this page match.'));
    expect(grid()).toHaveAttribute('aria-rowcount', '2');
    // Part 7's name is cut on the page; the detail loads it whole by the page's own row (6).
    await waitFor(() => expect(cellText(0, 2)).toContain('⋯'));
    await user.click(grid().querySelector('[data-row="0"][data-column="2"]') as HTMLElement);
    const detail = await screen.findByTestId('cell-detail');
    expect(within(detail).getByTestId('cell-value').textContent).toHaveLength(PAGE_CHARS);
    await user.click(within(detail).getByTestId('viewer-load-full'));
    await waitFor(() =>
      expect(within(detail).getByTestId('cell-value').textContent).toHaveLength(LONG_CHARS),
    );
    expect(log.cells).toEqual([{ page: 'vp1', row: 6, column: 'name' }]);
    expect(screen.getByTestId('viewer-full-done')).toHaveTextContent(
      `The whole value is shown (${LONG_CHARS.toLocaleString('en')} bytes).`,
    );
    expect(within(detail).queryByTestId('viewer-load-full')).toBeNull();
  });

  it('an answer for an object no longer shown is dropped', async () => {
    const slow = later();
    let held = true;
    const { fetcher } = server((call) => {
      if (held && call.url.includes('/rows?') && call.url.includes('name=part&')) {
        held = false;
        return slow.answer();
      }
      return null;
    });
    const { user } = await open(fetcher);
    await showData(user, 'part');
    await showData(user, 'material');
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–5; this is the last page.'));
    const viewer = createViewer();
    const stale = viewer.answer('/api/connections/c-sqlite/rows?schema=main&name=part&limit=100');
    await act(async () => {
      slow.send(stale as Response);
      await Promise.resolve();
    });
    expect(status()).toHaveTextContent('Rows 1–5');
    expect(grid()).toHaveAccessibleName('Data of main.material');
  });

  it('a profile switch drops the data and its late answer (C-1)', async () => {
    const slow = later();
    const { fetcher } = server((call) => (call.url.includes('/rows?') ? slow.answer() : null));
    const { user } = await open(fetcher);
    await showData(user, 'part');
    expect(status()).toHaveTextContent('Loading the page…');
    await user.selectOptions(screen.getByLabelText('Connection profile'), 'project:parts-pg');
    expect(screen.queryByTestId('viewer')).toBeNull();
    const viewer = createViewer();
    await act(async () => {
      slow.send(
        viewer.answer('/api/connections/c-sqlite/rows?schema=main&name=part&limit=100') as Response,
      );
      await Promise.resolve();
    });
    expect(screen.queryByRole('grid')).toBeNull();
    expect(screen.queryByTestId('explorer-workspace')).toBeNull();
  });

  it('a connection that is gone leaves the explorer and the header', async () => {
    const { fetcher } = server((call) =>
      call.url.includes('/rows?') ? problem('studio.connection_not_found', 404) : null,
    );
    const { user, shared } = await open(fetcher);
    expect(shared.state.active?.connectionId).toBe('c-sqlite');
    await showData(user, 'part', false);
    await waitFor(() => expect(screen.queryByTestId('explorer-workspace')).toBeNull());
    expect(screen.getByTestId('explorer-problem')).toBeVisible();
    expect(shared.state.active).toBeNull();
    expect(screen.getByTestId('explorer-open')).toHaveFocus();
  });

  it('a page the server no longer keeps offers the first page again', async () => {
    const { fetcher } = server((call) =>
      call.url.includes('after=') ? problem('studio.viewer_page_unknown', 404) : null,
    );
    const { user } = await open(fetcher);
    await showData(user, 'part');
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–100'));
    await user.click(screen.getByTestId('viewer-next'));
    expect(await screen.findByTestId('viewer-problem')).toHaveTextContent(
      'Studio no longer keeps this page.',
    );
    await user.click(screen.getByTestId('viewer-first-page'));
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–100'));
    expect(screen.queryByTestId('viewer-problem')).toBeNull();
  });

  it('an answer that is not a page is refused (fail closed)', async () => {
    const { fetcher } = server((call) =>
      call.url.includes('/rows?')
        ? json({ columns: [{ name: 'a' }], rows: [[1, 2]], page: 'p', paging: 'primary_key' })
        : null,
    );
    const { user } = await open(fetcher);
    await showData(user, 'part');
    expect(await screen.findByTestId('viewer-problem')).toHaveTextContent(
      "The server's answer could not be read",
    );
    expect(screen.queryByRole('grid')).toBeNull();
  });

  it('a table without a primary key says how it is paged; hostile values stay text', async () => {
    const { fetcher } = server();
    const { user } = await open(fetcher);
    await showData(user, new RegExp(`^${HOSTILE_TABLE.slice(0, 5)}`));
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–3; this is the last page.'));
    expect(screen.getByTestId('viewer-hint')).toHaveTextContent('No primary key');
    expect(document.querySelector('img')).toBeNull();
    await waitFor(() => expect(cellText(0, 0)).toBe(HOSTILE_VALUE.replace('‮', '⟨U+202E⟩')));
    await waitFor(() => expect(cellText(1, 0)).toBe('line one↵line two'));
    await waitFor(() => expect(cellText(2, 0)).toBe('NULL'));
  });

  it('sends nothing but reads (STU-026 stays locked)', async () => {
    const { fetcher, calls } = server();
    const { user } = await open(fetcher);
    await showData(user, 'part');
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–100'));
    await user.click(screen.getByTestId('viewer-next'));
    await waitFor(() => expect(status()).toHaveTextContent('Rows 101–200'));
    expect(calls.filter((call) => call.method !== 'GET')).toEqual([
      { url: '/api/connections', method: 'POST' },
    ]);
    const labels = screen.getAllByRole('button').map((button) => button.textContent ?? '');
    for (const word of [/\bedit\b/i, /delete/i, /insert/i, /save/i, /write/i]) {
      expect(labels.filter((label) => word.test(label))).toEqual([]);
    }
  });
});

describe('the explorer hands a statement to the SQL editor (TD-041)', () => {
  afterEach(() => {
    takeHandoff();
    window.history.pushState(null, '', '/');
  });

  it('"SELECT … LIMIT 100 in the SQL editor" opens the editor with the statement on this profile', async () => {
    const { fetcher } = server();
    const { user } = await open(fetcher, 'project:parts-pg');
    await user.click(screen.getByRole('treeitem', { name: 'measurement' }));
    await user.click(screen.getByTestId('explorer-to-editor'));
    expect(window.location.pathname).toBe('/sql');
    expect(takeHandoff()).toEqual({
      profileId: 'project:parts-pg',
      title: 'measurement',
      sql: 'SELECT * FROM "public"."measurement" LIMIT 100;',
    });
    // Only once.
    expect(takeHandoff()).toBeNull();
  });

  it('"Copy name" copies the qualified name', async () => {
    const { fetcher } = server();
    const { user } = await open(fetcher, 'project:parts-pg');
    const written: string[] = [];
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: (text: string) => Promise.resolve(void written.push(text)) },
    });
    await user.click(screen.getByRole('treeitem', { name: 'measurement' }));
    await user.click(screen.getByTestId('explorer-copy-name'));
    await waitFor(() => expect(written).toEqual(['public.measurement']));
    expect(screen.getByTestId('explorer-message')).toHaveTextContent('Name copied.');
  });
});
