/**
 * STU-012: the SQL editor page with the fake query engine of the e2e harness. CodeMirror needs a layout that jsdom
 * does not have, so a plain text area stands in for SqlEditor here; the real editor runs in e2e/editor.spec.ts and,
 * under the real CSP, in e2e/editor-backend.spec.ts.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { forwardRef, useImperativeHandle, useRef } from 'react';
import { vi } from 'vitest';

import { createQueryServer, type QueryLog } from '../../harness/queries';
import { PROFILES, SETTINGS } from '../../harness/profiles';
import { editorActions } from '../../keyboard/editorActions';
import { DRAFTS_KEY, draftsKey } from './drafts';
import { EditorView, queryProblemText, scriptFileName } from './EditorView';
import type { SqlEditorHandle, SqlEditorProps } from './SqlEditor';
import i18n from '../../i18n';

vi.mock('./SqlEditor', () => ({
  SqlEditor: forwardRef<SqlEditorHandle, SqlEditorProps>(function FakeEditor(props, ref) {
    const area = useRef<HTMLTextAreaElement>(null);
    useImperativeHandle(ref, () => ({
      selection: () => {
        const el = area.current as HTMLTextAreaElement;
        return { from: el.selectionStart, to: el.selectionEnd, head: el.selectionEnd };
      },
      select: (from: number, to: number) => {
        area.current?.focus();
        area.current?.setSelectionRange(from, to);
      },
      focus: () => area.current?.focus(),
    }));
    return (
      <textarea
        ref={area}
        aria-label={props.label}
        value={props.value}
        onChange={(event) => props.onChange(event.target.value)}
      />
    );
  }),
}));

// The grid's virtualizer measures its scroll element; jsdom has no layout (restored after each test).
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800);
});

afterEach(() => localStorage.clear());

interface Options {
  connectDelayMs?: number;
  projectPath?: string;
  profileTimeoutMs?: number;
}

function setup(draft?: { sql: string; profile?: string }, options: Options = {}) {
  if (draft !== undefined) {
    localStorage.setItem(
      draftsKey(options.projectPath ?? ''),
      JSON.stringify({
        tabs: [
          {
            id: 'd1',
            title: 'Query 1',
            profile_id: draft.profile ?? 'project:parts-dev',
            sql: draft.sql,
            saved_sql: '',
          },
        ],
        activeTab: 'd1',
      }),
    );
  }
  const server = createQueryServer({ connectDelayMs: options.connectDelayMs ?? 0 });
  const profiles = {
    ...PROFILES,
    profiles: PROFILES.profiles.map((item) =>
      item.environment === 'prod' && options.profileTimeoutMs !== undefined
        ? { ...item, statement_timeout_ms: options.profileTimeoutMs }
        : item,
    ),
  };
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    if (url === '/api/profiles') return new Response(JSON.stringify(profiles));
    if (url === '/api/project' && options.projectPath !== undefined) {
      return new Response(JSON.stringify({ path: options.projectPath, state: 'none' }));
    }
    if (url === '/api/settings') return new Response(JSON.stringify(SETTINGS));
    return (await server.handle(url, init)) ?? new Response(null, { status: 404 });
  };
  render(<EditorView fetcher={fetcher} />);
  return { log: server.log as QueryLog };
}

async function editor() {
  return screen.findByRole('textbox', { name: 'SQL of Query 1' });
}

describe('EditorView', () => {
  it('runs the statement at the cursor and shows its rows; the page never asks to write', async () => {
    const { log } = setup({ sql: 'select 1;\nselect * from generate_series(1, 3)' });
    const text = (await editor()) as HTMLTextAreaElement;
    text.setSelectionRange(15, 15);
    fireEvent.keyDown(text, { key: 'Enter', ctrlKey: true });
    await waitFor(() =>
      expect(screen.getByTestId('result-status')).toHaveTextContent('3 rows · 25 ms'),
    );
    expect(log.starts).toEqual([
      { sql: 'select * from generate_series(1, 3)', params: {}, maxRows: null },
    ]);
    const grid = screen.getByRole('grid', { name: 'Result 1' });
    expect(grid).toHaveAttribute('aria-rowcount', '4');
    await waitFor(() => expect(within(grid).getAllByRole('gridcell')[0]).toHaveTextContent('1'));
    expect(screen.getByTestId('result-announce')).toHaveTextContent('3 rows · 25 ms');
    expect(screen.queryByRole('button', { name: /unlock|write mode/i })).toBeNull();
  });

  it('runs only the selection when there is one', async () => {
    const { log } = setup({ sql: 'select 1; select 2' });
    const text = (await editor()) as HTMLTextAreaElement;
    text.setSelectionRange(10, 18);
    await userEvent.click(screen.getByTestId('editor-run'));
    await waitFor(() => expect(log.starts.map((s) => s.sql)).toEqual(['select 2']));
  });

  it('focuses the parameter without a value, and sends the typed ones', async () => {
    const user = userEvent.setup();
    const { log } = setup({ sql: 'select :flag, :doc' });
    await editor();
    await user.click(screen.getByTestId('editor-run'));
    const flag = screen.getByRole('textbox', { name: ':flag' });
    await waitFor(() => expect(flag).toHaveFocus());
    expect(flag).toHaveAttribute('aria-invalid', 'true');
    await user.selectOptions(screen.getByRole('combobox', { name: 'Type of :flag' }), 'boolean');
    await user.selectOptions(screen.getByRole('combobox', { name: 'Type of :doc' }), 'json');
    const doc = screen.getByRole('textbox', { name: ':doc' });
    fireEvent.change(doc, { target: { value: '{"a": 1}' } });
    await user.click(screen.getByTestId('editor-run'));
    await waitFor(() => expect(log.starts).toHaveLength(1));
    expect(log.starts[0]?.params).toEqual({ flag: true, doc: '{"a": 1}' });
  });

  it('a script stops when a statement fails while it runs, not only when it is refused', async () => {
    const user = userEvent.setup();
    const { log } = setup({ sql: 'select 1; select * from missing_table; select 3; select 4' });
    await editor();
    await user.click(screen.getByTestId('editor-run-script'));
    await waitFor(() =>
      expect(screen.getByTestId('result-status')).toHaveTextContent('4 statements, 1 failed'),
    );
    expect(log.starts.map((s) => s.sql)).toEqual(['select 1', 'select * from missing_table']);
    const messages = screen.getAllByTestId('message');
    expect(messages[1]).toHaveTextContent(/does not exist|not found/i);
    expect(messages[2]).toHaveTextContent('Not run');
    expect(messages[3]).toHaveTextContent('Not run');
  });

  it('numbers rows and cells for assistive technology (header row 1, data from 2)', async () => {
    setup({ sql: 'select * from generate_series(1, 3)' });
    fireEvent.keyDown(await editor(), { key: 'Enter', ctrlKey: true });
    const grid = await screen.findByRole('grid', { name: 'Result 1' });
    await waitFor(() => expect(within(grid).getAllByRole('gridcell')[0]).toHaveTextContent('1'));
    const rows = within(grid).getAllByRole('row');
    expect(rows[0]).toHaveAttribute('aria-rowindex', '1');
    expect(rows[1]).toHaveAttribute('aria-rowindex', '2');
    expect(within(rows[1] as HTMLElement).getAllByRole('gridcell')[0]).toHaveTextContent('1');
    expect(rows[3]).toHaveAttribute('aria-rowindex', '4');
    expect(within(rows[3] as HTMLElement).getAllByRole('gridcell')[0]).toHaveTextContent('3');
    expect(within(rows[1] as HTMLElement).getAllByRole('gridcell')[1]).toHaveAttribute(
      'aria-colindex',
      '2',
    );
  });

  it('a click opens the detail and keeps the focus in the grid; Enter moves it into the panel', async () => {
    const user = userEvent.setup();
    setup({ sql: 'select * from generate_series(1, 3)' });
    fireEvent.keyDown(await editor(), { key: 'Enter', ctrlKey: true });
    const grid = await screen.findByRole('grid', { name: 'Result 1' });
    await waitFor(() => expect(within(grid).getAllByRole('gridcell')[0]).toHaveTextContent('1'));
    await user.click(within(grid).getAllByRole('gridcell')[5] as HTMLElement);
    expect(screen.getByTestId('cell-value')).toHaveTextContent('2');
    expect(grid).toHaveFocus();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(grid).not.toHaveFocus());
    expect(document.activeElement?.tagName).toBe('H3');
  });

  it('runs a script in turn, stops at the first error and lists every statement', async () => {
    const user = userEvent.setup();
    const { log } = setup({ sql: 'select 1; delete from parts; select 3' });
    await editor();
    await user.click(screen.getByTestId('editor-run-script'));
    await waitFor(() =>
      expect(screen.getByTestId('result-status')).toHaveTextContent('3 statements, 1 failed'),
    );
    expect(log.starts.map((s) => s.sql)).toEqual(['select 1', 'delete from parts']);
    const messages = screen.getAllByTestId('message');
    expect(messages[1]).toHaveTextContent(/Studio runs only reading statements/);
    expect(messages[2]).toHaveTextContent('Not run');
    await user.click(
      within(messages[1] as HTMLElement).getByRole('button', { name: /Go to statement/ }),
    );
    const text = (await editor()) as HTMLTextAreaElement;
    expect(text).toHaveFocus();
    expect([text.selectionStart, text.selectionEnd]).toEqual([10, 27]);
  });

  it('cancels a running query', async () => {
    const user = userEvent.setup();
    const { log } = setup({ sql: 'select pg_sleep(60)' });
    await editor();
    await user.click(screen.getByTestId('editor-run'));
    await waitFor(() => expect(screen.getByTestId('editor-cancel')).toBeEnabled());
    expect(editorActions()?.running()).toBe(true);
    await user.click(screen.getByTestId('editor-cancel'));
    await waitFor(() =>
      expect(screen.getByTestId('result-status')).toHaveTextContent(/^Canceled after \d\.\d s\.$/),
    );
    expect(log.cancels).toHaveLength(1);
  });

  it('clears PROD results when the tab changes to another profile (M-1, L-1)', async () => {
    const user = userEvent.setup();
    const { log } = setup({
      sql: 'select * from generate_series(1, 2000)',
      profile: 'user:measure-central',
    });
    await editor();
    await user.click(screen.getByTestId('editor-run'));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Connect' }));
    await waitFor(() =>
      expect(screen.getByTestId('result-status')).toHaveTextContent(/^1,000 rows, capped/),
    );
    expect(screen.getByRole('grid')).toBeInTheDocument();
    expect(screen.getByTestId('load-more')).toBeInTheDocument();
    await user.selectOptions(screen.getByTestId('editor-profile'), 'project:parts-dev');
    // No PROD row stays under the DEV badge, and "Load more" cannot run the PROD statement on DEV.
    expect(screen.queryByRole('grid')).toBeNull();
    expect(screen.queryByTestId('load-more')).toBeNull();
    expect(screen.getByTestId('result-status')).toHaveTextContent('');
    expect(log.starts).toHaveLength(1);
  });

  it('a second run while connecting starts nothing more (M-2)', async () => {
    const { log } = setup({ sql: 'select 1' }, { connectDelayMs: 300 });
    const text = await editor();
    fireEvent.keyDown(text, { key: 'Enter', ctrlKey: true });
    fireEvent.keyDown(text, { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(screen.getByTestId('editor-cancel')).toBeEnabled());
    expect(screen.getByTestId('result-status')).toHaveTextContent('Connecting…');
    fireEvent.keyDown(text, { key: 'Enter', ctrlKey: true });
    await waitFor(() =>
      expect(screen.getByTestId('result-status')).toHaveTextContent('3 rows · 25 ms'),
    );
    expect(log.connections).toHaveLength(1);
    expect(log.starts).toHaveLength(1);
  });

  it('a cancel while connecting starts no query (M-2)', async () => {
    const user = userEvent.setup();
    const { log } = setup({ sql: 'select pg_sleep(60)' }, { connectDelayMs: 1_500 });
    await editor();
    await user.click(screen.getByTestId('editor-run'));
    await waitFor(() => expect(screen.getByTestId('editor-cancel')).toBeEnabled());
    await user.click(screen.getByTestId('editor-cancel'));
    await waitFor(
      () =>
        expect(screen.getByTestId('editor-notice')).toHaveTextContent(
          'Canceled before the query started.',
        ),
      { timeout: 3_000 },
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 100));
    });
    expect(log.connections).toHaveLength(1);
    expect(log.starts).toEqual([]);
    expect(screen.getByTestId('editor-run')).toBeEnabled();
  });

  it('closing the tab while connecting starts no query (M-2)', async () => {
    const user = userEvent.setup();
    const { log } = setup({ sql: 'select pg_sleep(60)' }, { connectDelayMs: 1_500 });
    await editor();
    await user.click(screen.getByTestId('editor-run'));
    await user.click(screen.getByRole('button', { name: 'Close Query 1' }));
    await user.click(
      within(screen.getByRole('dialog', { name: 'Script not saved' })).getByRole('button', {
        name: 'Discard',
      }),
    );
    // Past the end of the connection's delay.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1_800));
    });
    expect(log.connections).toHaveLength(1);
    expect(log.starts).toEqual([]);
    expect(screen.queryByTestId('editor-notice')).toHaveTextContent('');
  });

  it("D-02 names the profile's own timeout when it has one (L-4)", async () => {
    const user = userEvent.setup();
    setup({ sql: 'select 1', profile: 'user:measure-central' }, { profileTimeoutMs: 5_000 });
    await editor();
    await user.click(screen.getByTestId('editor-run'));
    expect(screen.getByRole('dialog', { name: 'Production database' })).toHaveTextContent(
      'queries are limited to 5 s',
    );
  });

  it('keeps the drafts per project (L-5)', async () => {
    setup({ sql: 'select 7' }, { projectPath: '/work/parts' });
    expect(await editor()).toHaveValue('select 7');
    expect(localStorage.getItem(DRAFTS_KEY)).toBeNull();
    fireEvent.change(await editor(), { target: { value: 'select 8' } });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 500));
    });
    expect(localStorage.getItem(draftsKey('/work/parts'))).toContain('select 8');
    expect(localStorage.getItem(DRAFTS_KEY)).toBeNull();
  });

  it('asks D-02 before the first PROD connection', async () => {
    const user = userEvent.setup();
    const { log } = setup({ sql: 'select 1', profile: 'user:measure-central' });
    await editor();
    await user.click(screen.getByTestId('editor-run'));
    const dialog = screen.getByRole('dialog', { name: 'Production database' });
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(log.connections).toEqual([]);
    await user.click(screen.getByTestId('editor-run'));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(log.connections).toEqual(['user:measure-central']));
  });

  it('asks D-06 before closing a changed tab, and keeps parameter values out of the drafts', async () => {
    const user = userEvent.setup();
    setup({ sql: 'select :secret' });
    await editor();
    fireEvent.change(screen.getByRole('textbox', { name: ':secret' }), {
      target: { value: 'hunter2' },
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 500));
    });
    expect(localStorage.getItem(DRAFTS_KEY)).toContain('select :secret');
    expect(localStorage.getItem(DRAFTS_KEY)).not.toContain('hunter2');
    await user.click(screen.getByRole('button', { name: 'Close Query 1' }));
    const dialog = screen.getByRole('dialog', { name: 'Script not saved' });
    await user.click(within(dialog).getByRole('button', { name: 'Discard' }));
    // The last tab closed: a new empty one takes its place.
    const tabs = screen.getAllByTestId('editor-tab');
    expect(tabs).toHaveLength(1);
    expect(tabs[0]).toHaveTextContent('Query 1');
    expect(screen.getByRole('textbox', { name: 'SQL of Query 1' })).toHaveValue('');
  });

  it('opens a new tab with Alt+T and offers its commands to the palette', async () => {
    setup({ sql: 'select 1' });
    await editor();
    fireEvent.keyDown(window, { key: '†', code: 'KeyT', altKey: true });
    await waitFor(() => expect(screen.getAllByTestId('editor-tab')).toHaveLength(2));
    expect(screen.getAllByTestId('editor-tab')[1]).toHaveAttribute('aria-current', 'true');
    expect(editorActions()).not.toBeNull();
  });
});

describe('texts', () => {
  it('give the read-only refusal of 08 section 4.4 and a fallback with the code', () => {
    const t = i18n.t.bind(i18n);
    expect(
      queryProblemText(t, { code: 'db.read_only', kind: 'database', status: 403, logId: null }),
    ).toBe(
      'This connection is read-only. The database refused the change (read-only transaction).',
    );
    expect(
      queryProblemText(t, { code: 'db.corrupt', kind: 'database', status: 500, logId: null }),
    ).toBe('The database refused the statement (db.corrupt).');
  });

  it('make a file name of a tab title', () => {
    expect(scriptFileName('a/b:c?')).toBe('a_b_c_.sql');
    expect(scriptFileName('  ')).toBe('query.sql');
  });
});
