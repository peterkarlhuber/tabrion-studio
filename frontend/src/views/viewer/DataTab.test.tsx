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
  MAX_FILTERS,
  MAX_LIMIT,
  MAX_QUERY_BYTES,
  MAX_TOKEN_CHARS,
  pageQuery,
} from '../../api/viewer';
import { rowBytes } from '../editor/rowCache';
import { FIRST_READ_DELAY } from './DataTab';
import { PAGE_BYTES } from './pageRows';
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

type Call = { url: string; method: string; signal?: AbortSignal };

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const problem = (code: string, status: number) => json({ code, kind: 'x', status }, status);

/** The explorer's and the viewer's fake answers; `before` answers first (null: not this one). */
function server(before: (call: Call) => Response | Promise<Response> | null = () => null) {
  const viewer = createViewer();
  const calls: Call[] = [];
  const fetcher = async (input: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = { url: String(input), method: init?.method ?? 'GET' };
    calls.push(call);
    if (init?.signal) call.signal = init.signal;
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
    // "Table order" has no direction: it goes back to ascending (I-3).
    await user.selectOptions(screen.getByTestId('viewer-sort'), '');
    await waitFor(() => expect(log.pages.at(-1)).toMatchObject({ sort: null, desc: false }));
    expect(screen.getByTestId('viewer-direction')).toHaveValue('asc');
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

  // M-1: the server refuses a request head over 16 KB with a plain 400; the form says so before anything is sent.
  it('filters too long for one request are refused in the form, counted in encoded bytes', async () => {
    const { fetcher, log } = server();
    const { user } = await open(fetcher);
    await showData(user, 'part');
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–100'));
    await user.click(screen.getByTestId('viewer-add-filter'));
    await user.selectOptions(screen.getByLabelText('Column of filter 1'), 'name');
    await user.selectOptions(screen.getByLabelText('Operator of filter 1'), 'contains');
    const value = screen.getByLabelText('Value of filter 1');
    // 1,400 characters, but 12,600 bytes once percent-encoded (three bytes of UTF-8 each).
    await user.click(value);
    await user.paste('€'.repeat(1400));
    const asked = log.pages.length;
    await user.click(screen.getByTestId('viewer-apply'));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'These filters are too long for one request.',
    );
    expect(log.pages).toHaveLength(asked);
    expect(screen.queryByTestId('viewer-filters-applied')).toBeNull();
    await user.clear(value);
    await user.type(value, 'Part 1');
    await user.click(screen.getByTestId('viewer-apply'));
    await waitFor(() => expect(log.pages).toHaveLength(asked + 1));
    expect(screen.queryByRole('alert')).toBeNull();
  });

  // N-1: the form counts the largest page and the longest `next` token, so a walk it lets through can go on.
  it('filters exactly at the bound are applied and Next still works; one byte more is refused', async () => {
    const { fetcher, log } = server();
    const { user } = await open(fetcher);
    await showData(user, 'part');
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–100'));
    const longest = (value: string) =>
      pageQuery('main', 'part', {
        limit: MAX_LIMIT,
        sort: null,
        desc: false,
        filters: [{ column: 'name', op: 'ne', value }],
        after: 'x'.repeat(MAX_TOKEN_CHARS),
      }).length;
    // "€" takes 9 bytes once encoded; ASCII letters fill the rest, so the value stays under the field's 4,096.
    const room = MAX_QUERY_BYTES - longest('');
    const value = '€'.repeat(Math.floor(room / 9)) + 'a'.repeat(room % 9);
    expect(longest(value)).toBe(MAX_QUERY_BYTES);
    await user.click(screen.getByTestId('viewer-add-filter'));
    await user.selectOptions(screen.getByLabelText('Column of filter 1'), 'name');
    await user.selectOptions(screen.getByLabelText('Operator of filter 1'), 'ne');
    const field = screen.getByLabelText('Value of filter 1');
    await user.click(field);
    await user.paste(value);
    await user.click(screen.getByTestId('viewer-apply'));
    await waitFor(() =>
      expect(screen.getByTestId('viewer-filters-applied')).toHaveTextContent('(1 applied)'),
    );
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–100; more rows follow.'));
    await user.click(screen.getByTestId('viewer-next'));
    await waitFor(() => expect(status()).toHaveTextContent('Rows 101–200'));
    expect(log.pages.at(-1)?.after).toMatch(/^vp\d+$/);
    expect(screen.queryByTestId('viewer-problem')).toBeNull();
    const asked = log.pages.length;
    await user.type(field, 'a');
    await user.click(screen.getByTestId('viewer-apply'));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'These filters are too long for one request.',
    );
    expect(log.pages).toHaveLength(asked);
  });

  it('at most 20 filters, and the add button says why', async () => {
    const { fetcher } = server();
    const { user } = await open(fetcher);
    await showData(user, 'part');
    const add = screen.getByTestId('viewer-add-filter');
    for (let count = 0; count < MAX_FILTERS; count += 1) await user.click(add);
    expect(screen.getAllByTestId('viewer-filter')).toHaveLength(MAX_FILTERS);
    expect(add).toHaveAttribute('aria-disabled', 'true');
    expect(add).toHaveAccessibleDescription('At most 20 filters.');
    await user.click(add);
    expect(screen.getAllByTestId('viewer-filter')).toHaveLength(MAX_FILTERS);
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
    // device: BIGINT and TEXT; a TEXT value can be 64 K characters (256 KB above U+FFFF): 64 MB hold 255 such rows.
    await waitFor(() => expect(log.pages.at(-1)?.limit).toBe(255));
    expect(screen.getByTestId('viewer-limited')).toHaveTextContent(
      'Pages here hold at most 255 rows',
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

  // M-2 of stu-013-page-review.md: the guard of one tab (`mine !== request.current`), not only the unmount.
  it('an older page that arrives after a newer request is dropped', async () => {
    const slow = later();
    let first = '';
    const { fetcher } = server((call) => {
      if (first === '' && call.url.includes('/rows?')) {
        first = call.url;
        return slow.answer();
      }
      return null;
    });
    const { user } = await open(fetcher);
    await showData(user, 'part');
    await waitFor(() => expect(first).not.toBe(''));
    // Sorted by name, "Long part …" (id 7) comes first; in table order it is id 1.
    await user.selectOptions(screen.getByTestId('viewer-sort'), 'name');
    await waitFor(() => expect(cellText(0, 0)).toBe('7'));
    const stale = createViewer().answer(first);
    await act(async () => {
      slow.send(stale as Response);
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(cellText(0, 0)).toBe('7');
    expect(screen.getByTestId('viewer-sort')).toHaveValue('name');
    expect(screen.queryByTestId('viewer-problem')).toBeNull();
  });

  it('an aborted request is not shown as a problem while the newer one is on its way', async () => {
    // A real fetch rejects when its signal aborts; the read then answers `OFFLINE`, which must not be shown.
    const second = later();
    let rows = 0;
    let secondUrl = '';
    let firstSignal: AbortSignal | undefined;
    const { fetcher } = server((call) => {
      if (!call.url.includes('/rows?')) return null;
      rows += 1;
      if (rows === 1) {
        firstSignal = call.signal;
        return new Promise<Response>((_, reject) => {
          call.signal?.addEventListener('abort', () =>
            reject(new DOMException('The request was aborted.', 'AbortError')),
          );
        });
      }
      if (rows === 2) {
        secondUrl = call.url;
        return second.answer();
      }
      return null;
    });
    const { user } = await open(fetcher);
    await showData(user, 'part');
    await waitFor(() => expect(rows).toBe(1));
    expect(firstSignal?.aborted).toBe(false);
    await user.selectOptions(screen.getByTestId('viewer-sort'), 'name');
    await waitFor(() => expect(rows).toBe(2));
    // The older request is aborted, not only ignored (N-2): its transfer and the server's work stop.
    expect(firstSignal?.aborted).toBe(true);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(screen.queryByTestId('viewer-problem')).toBeNull();
    expect(status()).toHaveTextContent('Loading the page');
    const answer = createViewer().answer(secondUrl);
    await act(async () => {
      second.send(answer as Response);
      await Promise.resolve();
    });
    await waitFor(() => expect(cellText(0, 0)).toBe('7'));
    expect(screen.queryByTestId('viewer-problem')).toBeNull();
  });

  // L-1: SQLite does not enforce declared types; a later walk stays within the budget at the widest row seen.
  it('a new walk asks for fewer rows after a row wider than its types', async () => {
    let widened = false;
    let wide = 0;
    const { fetcher, log } = server((call) => {
      if (widened || !call.url.includes('/rows?')) return null;
      widened = true;
      const body = createViewer().answer(call.url);
      return (async () => {
        const page = (await (body as Response).json()) as { rows: unknown[][] };
        // part_number is VARCHAR(40), but this SQLite row holds 400 000 characters in it.
        const row = page.rows[0] ?? [];
        row[1] = 'x'.repeat(400_000);
        wide = rowBytes(row);
        return json(page);
      })();
    });
    const { user } = await open(fetcher);
    await showData(user, 'part');
    await waitFor(() => expect(status()).toHaveTextContent('Rows 1–100'));
    expect(screen.queryByTestId('viewer-limited')).toBeNull();
    await user.click(screen.getByTestId('viewer-refresh'));
    const expected = Math.floor(PAGE_BYTES / wide);
    expect(expected).toBeLessThan(100);
    await waitFor(() => expect(log.pages.at(-1)?.limit).toBe(expected));
    expect(log.pages.at(-1)?.after).toBeNull();
    expect(screen.getByTestId('viewer-limited')).toHaveTextContent(
      `Pages here hold at most ${expected} rows`,
    );
  });

  // L-3: the first page is asked for only once the tab has stayed open.
  it('passing through the Data tab asks for nothing', async () => {
    const { fetcher, log } = server();
    const { user } = await open(fetcher);
    await showData(user, 'part', false);
    await user.click(screen.getByRole('tab', { name: 'Columns' }));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, FIRST_READ_DELAY * 3));
    });
    expect(log.pages).toEqual([]);
    await user.click(screen.getByRole('tab', { name: 'Data' }));
    await waitFor(() => expect(log.pages).toHaveLength(1));
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
