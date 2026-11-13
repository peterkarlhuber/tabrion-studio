/**
 * STU-012: the SQL editor page with the fake query engine of the e2e harness. CodeMirror needs a layout that jsdom
 * does not have, so a plain text area stands in for SqlEditor here; the real editor runs in e2e/editor.spec.ts and,
 * under the real CSP, in e2e/editor-backend.spec.ts.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { CompletionContext, type CompletionResult } from '@codemirror/autocomplete';
import { EditorState } from '@codemirror/state';
import userEvent from '@testing-library/user-event';
import { forwardRef, useImperativeHandle, useRef } from 'react';
import { vi } from 'vitest';

import { createQueryServer, type QueryLog } from '../../harness/queries';
import { PROFILES, SETTINGS } from '../../harness/profiles';
import { editorActions } from '../../keyboard/editorActions';
import type { SaveFile } from '../../lib/download';
import { DRAFTS_KEY, draftsKey } from './drafts';
import { handToEditor } from './handoff';
import { EditorView, queryProblemText, scriptFileName } from './EditorView';
import type { SqlEditorHandle, SqlEditorProps } from './SqlEditor';
import i18n from '../../i18n';
import {
  ConnectionSessionContext,
  NO_CONNECTION_SESSION,
  type ConnectionSession,
  type Reconnected,
} from '../../workspace';

/** How often the page closed the editor's completion list (B-6 of ux-acceptance.md). */
const completionClosed = vi.hoisted(() => ({ count: 0 }));
/** The props the editor got last (STU-020: the completion namespace and source). */
const lastProps = vi.hoisted(() => ({ current: null as SqlEditorProps | null }));

vi.mock('./SqlEditor', () => ({
  SqlEditor: forwardRef<SqlEditorHandle, SqlEditorProps>(function FakeEditor(props, ref) {
    const area = useRef<HTMLTextAreaElement>(null);
    lastProps.current = props;
    useImperativeHandle(ref, () => ({
      selection: () => {
        const el = area.current as HTMLTextAreaElement;
        return { from: el.selectionStart, to: el.selectionEnd, head: el.selectionEnd };
      },
      select: (from: number, to: number) => {
        area.current?.focus();
        area.current?.setSelectionRange(from, to);
      },
      replace: (from: number, to: number, text: string) => {
        props.onChange(`${props.value.slice(0, from)}${text}${props.value.slice(to)}`);
        area.current?.focus();
      },
      closeCompletion: () => {
        completionClosed.count += 1;
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
  /** Delays the answer to starting a query (a cancel while it is on its way). */
  startDelayMs?: number;
  /** Answers before the fake server (a changed profile, a stale connection, the connection's environment). */
  before?: (url: string, init?: RequestInit) => Response | null;
  /** Where an export goes (TD-043). */
  saveExport?: SaveFile;
  /** The page's connection session (TD-034); none by default. */
  session?: ConnectionSession;
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
  // The profiles as the page last read them; the fake server's connection answers name their environment.
  let seen: { profiles: { id: string; environment: string }[] } | null = null;
  const server = createQueryServer({
    connectDelayMs: options.connectDelayMs ?? 0,
    startDelayMs: options.startDelayMs ?? 0,
    environmentOf: (id) => seen?.profiles.find((item) => item.id === id)?.environment,
  });
  const profiles = {
    ...PROFILES,
    profiles: PROFILES.profiles.map((item) =>
      item.environment === 'prod' && options.profileTimeoutMs !== undefined
        ? { ...item, statement_timeout_ms: options.profileTimeoutMs }
        : item,
    ),
  };
  const requests: { method: string; url: string }[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    requests.push({ method: init?.method ?? 'GET', url });
    const early = options.before?.(url, init) ?? null;
    if (early !== null) {
      if (url === '/api/profiles' && early.ok) seen = (await early.clone().json()) as typeof seen;
      return early;
    }
    if (url === '/api/profiles') {
      seen = profiles;
      return new Response(JSON.stringify(profiles));
    }
    if (url === '/api/project' && options.projectPath !== undefined) {
      return new Response(JSON.stringify({ path: options.projectPath, state: 'none' }));
    }
    if (url === '/api/settings') return new Response(JSON.stringify(SETTINGS));
    return (await server.handle(url, init)) ?? new Response(null, { status: 404 });
  };
  const view = <EditorView fetcher={fetcher} saveExport={options.saveExport} />;
  render(
    options.session === undefined ? (
      view
    ) : (
      <ConnectionSessionContext.Provider value={options.session}>
        {view}
      </ConnectionSessionContext.Provider>
    ),
  );
  return { log: server.log as QueryLog, requests };
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

  it('a cancel while the start request is on its way cancels the query once it has an id', async () => {
    const user = userEvent.setup();
    const { log, requests } = setup({ sql: 'select pg_sleep(60)' }, { startDelayMs: 500 });
    await editor();
    await user.click(screen.getByTestId('editor-run'));
    // The statement shows as running while POST /api/queries is still unanswered.
    await waitFor(() => expect(log.starts).toHaveLength(1));
    await waitFor(() => expect(screen.getByTestId('editor-cancel')).toBeEnabled());
    await user.click(screen.getByTestId('editor-cancel'));
    expect(log.cancels).toEqual([]);
    await waitFor(
      () =>
        expect(screen.getByTestId('result-status')).toHaveTextContent(
          /^Canceled after \d\.\d s\.$/,
        ),
      { timeout: 3_000 },
    );
    expect(log.cancels).toHaveLength(1);
    expect(requests.filter((r) => r.url.endsWith('/cancel'))).toHaveLength(1);
    expect(editorActions()?.running()).toBe(false);
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

  describe('a profile that changed on the server since the page read it (C-5)', () => {
    const asProd = () =>
      new Response(
        JSON.stringify({
          ...PROFILES,
          profiles: PROFILES.profiles.map((item) =>
            item.id === 'project:parts-dev' ? { ...item, environment: 'prod' } : item,
          ),
        }),
      );

    it('first connection: nothing is opened, the page takes the new profile and the next run asks D-02', async () => {
      const user = userEvent.setup();
      let reads = 0;
      const { log } = setup(
        { sql: 'select 1' },
        {
          before: (url) => {
            if (url !== '/api/profiles') return null;
            reads += 1;
            // The page read it as DEV; it is PROD now.
            return reads === 1 ? null : asProd();
          },
        },
      );
      await editor();
      await user.click(screen.getByTestId('editor-run'));
      await waitFor(() =>
        expect(screen.getByTestId('editor-notice')).toHaveTextContent(
          'The profile parts-dev changed since this page read it (now PROD). Nothing was run on it',
        ),
      );
      expect(log.connections).toEqual([]);
      expect(log.starts).toEqual([]);
      expect(screen.queryByRole('dialog')).toBeNull();
      // The toolbar shows the profile as it is now, and Run asks D-02.
      expect(screen.getAllByTestId('env-badge').at(-1)).toHaveTextContent('PROD');
      await user.click(screen.getByTestId('editor-run'));
      const dialog = await screen.findByRole('dialog', { name: 'Production database' });
      await user.click(within(dialog).getByRole('button', { name: 'Connect' }));
      await waitFor(() => expect(log.starts).toHaveLength(1));
      expect(log.connections).toEqual(['project:parts-dev']);
    });

    it('a stale connection is not opened again silently: the run stops and the next one asks D-02', async () => {
      const user = userEvent.setup();
      let changed = false;
      const { log } = setup(
        { sql: 'select 1' },
        {
          before: (url, init) => {
            if (!changed) return null;
            if (url === '/api/profiles') return asProd();
            if (url === '/api/queries' && init?.method === 'POST') {
              return new Response(
                JSON.stringify({ code: 'studio.connection_stale', status: 409 }),
                {
                  status: 409,
                },
              );
            }
            return null;
          },
        },
      );
      await editor();
      await user.click(screen.getByTestId('editor-run'));
      await waitFor(() =>
        expect(screen.getByTestId('result-status')).toHaveTextContent('3 rows · 25 ms'),
      );
      changed = true;
      await user.click(screen.getByTestId('editor-run'));
      await waitFor(() =>
        expect(screen.getByTestId('editor-notice')).toHaveTextContent(
          'The profile parts-dev changed since this page read it (now PROD)',
        ),
      );
      // No second connection, no dialog, and the DEV rows are gone.
      expect(log.connections).toEqual(['project:parts-dev']);
      expect(screen.queryByRole('dialog')).toBeNull();
      expect(screen.queryByRole('grid')).toBeNull();
      await user.click(screen.getByTestId('editor-run'));
      expect(await screen.findByRole('dialog', { name: 'Production database' })).toBeVisible();
    });

    it('TD-038: the server refuses a PROD open the page did not confirm; the page takes the profile as it is and the next run asks D-02', async () => {
      const user = userEvent.setup();
      let refused = false;
      const bodies: unknown[] = [];
      const { log } = setup(
        { sql: 'select 1' },
        {
          before: (url, init) => {
            if (url === '/api/connections' && init?.method === 'POST') {
              bodies.push(JSON.parse(String(init.body)));
              if (!refused) {
                // The profile became PROD after the editor's last read, just before the open.
                refused = true;
                return new Response(
                  JSON.stringify({
                    code: 'studio.prod_confirmation_required',
                    kind: 'database',
                    status: 409,
                  }),
                  { status: 409 },
                );
              }
            }
            if (url === '/api/profiles' && refused) return asProd();
            return null;
          },
        },
      );
      await editor();
      await user.click(screen.getByTestId('editor-run'));
      expect(await screen.findByTestId('editor-connect-problem')).toHaveTextContent(
        'This profile is marked as PROD now.',
      );
      expect(log.starts).toEqual([]);
      await waitFor(() =>
        expect(screen.getAllByTestId('env-badge').at(-1)).toHaveTextContent('PROD'),
      );
      await user.click(screen.getByTestId('editor-run'));
      const dialog = await screen.findByRole('dialog', { name: 'Production database' });
      await user.click(within(dialog).getByRole('button', { name: 'Connect' }));
      await waitFor(() => expect(log.starts).toHaveLength(1));
      expect(bodies).toEqual([
        { profile_id: 'project:parts-dev', confirmed_environment: 'dev' },
        { profile_id: 'project:parts-dev', confirmed_environment: 'prod' },
      ]);
    });

    it('backstop: a connection whose environment differs from the guarded profile is closed, and nothing runs', async () => {
      const user = userEvent.setup();
      const { log, requests } = setup(
        { sql: 'select 1' },
        {
          before: (url, init) =>
            url === '/api/connections' && init?.method === 'POST'
              ? new Response(
                  JSON.stringify({
                    connection_id: 'c-x',
                    profile_id: 'project:parts-dev',
                    environment: 'prod',
                  }),
                  { status: 201 },
                )
              : null,
        },
      );
      await editor();
      await user.click(screen.getByTestId('editor-run'));
      await waitFor(() =>
        expect(screen.getByTestId('editor-notice')).toHaveTextContent('(now PROD)'),
      );
      expect(log.starts).toEqual([]);
      expect(requests).toContainEqual({ method: 'DELETE', url: '/api/connections' });
    });

    it('backstop (L-9): an answer without an environment counts as a mismatch', async () => {
      const user = userEvent.setup();
      const { log, requests } = setup(
        { sql: 'select 1' },
        {
          before: (url, init) =>
            url === '/api/connections' && init?.method === 'POST'
              ? new Response(
                  JSON.stringify({ connection_id: 'c-x', profile_id: 'project:parts-dev' }),
                  { status: 201 },
                )
              : null,
        },
      );
      await editor();
      await user.click(screen.getByTestId('editor-run'));
      await waitFor(() =>
        expect(screen.getByTestId('editor-notice')).toHaveTextContent(
          'The server did not say which environment the connection to parts-dev is in',
        ),
      );
      expect(log.starts).toEqual([]);
      expect(requests).toContainEqual({ method: 'DELETE', url: '/api/connections' });
    });

    it('opens nothing when the profiles cannot be read again', async () => {
      const user = userEvent.setup();
      let reads = 0;
      const { log, requests } = setup(
        { sql: 'select 1' },
        {
          before: (url) => {
            if (url !== '/api/profiles') return null;
            reads += 1;
            return reads > 1 ? new Response('{}', { status: 500 }) : null;
          },
        },
      );
      await editor();
      await user.click(screen.getByTestId('editor-run'));
      expect(await screen.findByTestId('editor-connect-problem')).toHaveTextContent(
        'The local server does not answer.',
      );
      expect(requests.filter((r) => r.url === '/api/connections')).toEqual([]);
      expect(log.connections).toEqual([]);
      expect(log.starts).toEqual([]);
    });
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

  it('opens a statement the explorer handed over in a new tab, once, and runs nothing (TD-041)', async () => {
    handToEditor({
      profileId: 'user:measure-central',
      title: 'measurement',
      sql: 'SELECT * FROM "public"."measurement" LIMIT 100;',
    });
    const { log } = setup({ sql: 'select 1' });
    const text = await screen.findByRole('textbox', { name: 'SQL of measurement' });
    expect(text).toHaveValue('SELECT * FROM "public"."measurement" LIMIT 100;');
    const tabs = screen.getAllByTestId('editor-tab');
    expect(tabs).toHaveLength(2);
    expect(tabs[1]).toHaveAttribute('aria-current', 'true');
    expect(screen.getByTestId('editor-profile')).toHaveValue('user:measure-central');
    expect(log.connections).toEqual([]);
    expect(log.starts).toEqual([]);
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

describe('EditorView: export (TD-043)', () => {
  it('exports a finished result: its statement and parameters, on its connection; the focus comes back', async () => {
    const user = userEvent.setup();
    const save = vi.fn<SaveFile>();
    const { log } = setup(
      { sql: 'select * from generate_series(1, 4) where n > :min' },
      { saveExport: save },
    );
    await editor();
    // No result yet: no export.
    expect(screen.queryByTestId('export-open')).toBeNull();
    await user.click(screen.getByTestId('editor-run'));
    const min = screen.getByRole('textbox', { name: ':min' });
    await waitFor(() => expect(min).toHaveFocus());
    await user.selectOptions(screen.getByRole('combobox', { name: 'Type of :min' }), 'number');
    fireEvent.change(min, { target: { value: '0' } });
    await user.click(screen.getByTestId('editor-run'));
    await waitFor(() =>
      expect(screen.getByTestId('result-status')).toHaveTextContent('4 rows · 25 ms'),
    );
    const open = screen.getByRole('button', { name: 'Export…' });
    await user.click(open);
    const dialog = screen.getByRole('dialog', { name: 'Export' });
    expect(dialog).toHaveAccessibleDescription(
      'Export result 1 of Query 1 as a file. The export only reads.',
    );
    expect(
      within(dialog).getByRole('radio', { name: 'The fetched rows of the result (4)' }),
    ).toBeChecked();
    await user.click(within(dialog).getByRole('button', { name: 'Export' }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(log.exports).toEqual([
      {
        connectionId: 'c-project:parts-dev',
        format: 'csv',
        sql: 'select * from generate_series(1, 4) where n > :min',
        params: { min: 0 },
        maxRows: 4,
        escapeFormulas: true,
        aborted: false,
      },
    ]);
    // No new connection for the export.
    expect(log.connections).toEqual(['project:parts-dev']);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByTestId('editor-notice')).toHaveTextContent(/^Export saved: export\.csv \(/);
    expect(open).toHaveFocus();
  });

  it('exports the statement and parameter as they ran, not the editor text or field typed since (C-1)', async () => {
    const user = userEvent.setup();
    const save = vi.fn<SaveFile>();
    const ran = 'select * from generate_series(1, 4) where n > :min';
    const { log } = setup({ sql: ran }, { saveExport: save });
    const text = (await editor()) as HTMLTextAreaElement;
    await user.click(screen.getByTestId('editor-run'));
    const min = screen.getByRole('textbox', { name: ':min' });
    await waitFor(() => expect(min).toHaveFocus());
    await user.selectOptions(screen.getByRole('combobox', { name: 'Type of :min' }), 'number');
    fireEvent.change(min, { target: { value: '0' } });
    await user.click(screen.getByTestId('editor-run'));
    await waitFor(() =>
      expect(screen.getByTestId('result-status')).toHaveTextContent('4 rows · 25 ms'),
    );
    // After the run: another statement in the editor (a different table, a write after it) and another value.
    const typed =
      "select * from generate_series(1, 9) where n > :min; update parts set material = 'X'";
    fireEvent.change(text, { target: { value: typed } });
    expect(text).toHaveValue(typed);
    fireEvent.change(screen.getByRole('textbox', { name: ':min' }), { target: { value: '7' } });
    expect(screen.getByRole('textbox', { name: ':min' })).toHaveValue('7');
    await user.click(screen.getByRole('button', { name: 'Export…' }));
    const dialog = screen.getByRole('dialog', { name: 'Export' });
    await user.click(within(dialog).getByRole('button', { name: 'Export' }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(log.exports).toHaveLength(1);
    expect(log.exports[0]?.sql).toBe(ran);
    expect(log.exports[0]?.params).toEqual({ min: 0 });
    expect(log.exports[0]?.maxRows).toBe(4);
    // The file holds the ran statement's rows (4, not 9).
    const csv = await (save.mock.calls[0]?.[0] as Blob).text();
    expect(csv.split('\r\n').filter((line) => line !== '')).toHaveLength(5);
  });

  it('offers no export on the messages, for a failed statement, or while a statement runs', async () => {
    const user = userEvent.setup();
    setup({ sql: 'select missing_table;\nselect pg_sleep(60)' });
    const text = (await editor()) as HTMLTextAreaElement;
    text.setSelectionRange(0, 0);
    await user.click(screen.getByTestId('editor-run'));
    await waitFor(() => expect(screen.getByTestId('result-status')).toHaveTextContent(/./));
    await waitFor(() => expect(screen.getByTestId('editor-run')).not.toBeDisabled());
    expect(screen.queryByTestId('export-open')).toBeNull();
    text.setSelectionRange(text.value.length, text.value.length);
    await user.click(screen.getByTestId('editor-run'));
    await waitFor(() => expect(screen.getByTestId('editor-cancel')).not.toBeDisabled());
    expect(screen.queryByTestId('export-open')).toBeNull();
    await user.click(screen.getByTestId('editor-cancel'));
    await waitFor(() => expect(screen.getByTestId('editor-run')).not.toBeDisabled());
    expect(screen.queryByTestId('export-open')).toBeNull();
  });

  it('PROD: the export goes through the shared D-02 guard, confirmed by the run, so it asks nothing new', async () => {
    const user = userEvent.setup();
    const save = vi.fn<SaveFile>();
    const { log } = setup(
      { sql: 'select * from generate_series(1, 2)', profile: 'user:measure-central' },
      { saveExport: save },
    );
    await editor();
    await user.click(screen.getByTestId('editor-run'));
    const prod = screen.getByRole('dialog', { name: 'Production database' });
    await user.click(within(prod).getByRole('button', { name: 'Connect' }));
    await waitFor(() =>
      expect(screen.getByTestId('result-status')).toHaveTextContent('2 rows · 25 ms'),
    );
    await user.click(screen.getByRole('button', { name: 'Export…' }));
    // This test renders the page without the shell's page session, so D-02 is asked again before the export.
    const again = screen.getByRole('dialog', { name: 'Production database' });
    await user.click(within(again).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog', { name: 'Export' })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Export…' }));
    await user.click(
      within(screen.getByRole('dialog', { name: 'Production database' })).getByRole('button', {
        name: 'Connect',
      }),
    );
    const dialog = screen.getByRole('dialog', { name: 'Export' });
    await user.click(within(dialog).getByRole('button', { name: 'Export' }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(log.exports[0]?.connectionId).toBe('c-user:measure-central');
    expect(log.connections).toEqual(['user:measure-central']);
  });

  it("shows an export refusal in the dialog in the page's words", async () => {
    const user = userEvent.setup();
    const save = vi.fn<SaveFile>();
    setup({ sql: 'select export_busy' }, { saveExport: save });
    await editor();
    await user.click(screen.getByTestId('editor-run'));
    await waitFor(() =>
      expect(screen.getByTestId('result-status')).toHaveTextContent('3 rows · 25 ms'),
    );
    await user.click(screen.getByRole('button', { name: 'Export…' }));
    await user.click(
      within(screen.getByRole('dialog', { name: 'Export' })).getByRole('button', {
        name: 'Export',
      }),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Another export is running on this connection, or two are running already.',
    );
    expect(save).not.toHaveBeenCalled();
  });
});

describe('EditorView: a lost connection (TD-034)', () => {
  function session() {
    const listeners = new Set<(event: Reconnected) => void>();
    const value: ConnectionSession = {
      ...NO_CONNECTION_SESSION,
      markLost: vi.fn(),
      prodConfirmed: () => true,
      onReconnected: (listener) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
    };
    const reconnect = (event: Reconnected) => {
      for (const listener of listeners) listener(event);
    };
    return { value, reconnect };
  }

  it('reports a query that failed because the database stopped answering', async () => {
    const user = userEvent.setup();
    const { value } = session();
    setup({ sql: 'select connection_lost()' }, { session: value });
    await editor();
    await user.click(screen.getByTestId('editor-run'));
    await waitFor(() =>
      expect(value.markLost).toHaveBeenCalledWith('c-project:parts-dev', 'db.connection'),
    );
  });

  it('runs on the new connection after Reconnect replaced the old one', async () => {
    const user = userEvent.setup();
    const { value, reconnect } = session();
    const starts: string[] = [];
    setup(
      { sql: 'select 1' },
      {
        session: value,
        before: (url, init) => {
          if (url === '/api/queries' && init?.method === 'POST') {
            starts.push((JSON.parse(String(init.body)) as { connection_id: string }).connection_id);
          }
          return null;
        },
      },
    );
    await editor();
    await user.click(screen.getByTestId('editor-run'));
    await waitFor(() => expect(starts).toEqual(['c-project:parts-dev']));
    await waitFor(() =>
      expect(screen.getByTestId('result-status')).toHaveTextContent('3 rows · 25 ms'),
    );
    act(() =>
      reconnect({ profileId: 'project:parts-dev', from: 'c-project:parts-dev', to: 'c-new' }),
    );
    await user.click(screen.getByTestId('editor-run'));
    await waitFor(() => expect(starts).toEqual(['c-project:parts-dev', 'c-new']));
  });

  it('does not open a PROD connection again by itself when the server closed it: D-02 belongs to Reconnect', async () => {
    const user = userEvent.setup();
    const { value } = session();
    let closed = false;
    const { log } = setup(
      { sql: 'select 1', profile: 'user:measure-central' },
      {
        session: value,
        before: (url, init) =>
          closed && url === '/api/queries' && init?.method === 'POST'
            ? new Response(
                JSON.stringify({
                  code: 'studio.connection_not_found',
                  kind: 'not_found',
                  status: 404,
                }),
                { status: 404 },
              )
            : null,
      },
    );
    await editor();
    await user.click(screen.getByTestId('editor-run'));
    await waitFor(() => expect(log.connections).toEqual(['user:measure-central']));
    await waitFor(() =>
      expect(screen.getByTestId('result-status')).toHaveTextContent('3 rows · 25 ms'),
    );
    closed = true;
    await user.click(screen.getByTestId('editor-run'));
    await waitFor(() => expect(screen.getByTestId('result-status')).not.toHaveTextContent('25 ms'));
    expect(log.connections).toEqual(['user:measure-central']);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('opens a DEV connection again by itself, as before', async () => {
    const user = userEvent.setup();
    const { value } = session();
    let closed = false;
    const { log } = setup(
      { sql: 'select 1' },
      {
        session: value,
        before: (url, init) => {
          if (closed && url === '/api/queries' && init?.method === 'POST') {
            closed = false;
            return new Response(
              JSON.stringify({
                code: 'studio.connection_not_found',
                kind: 'not_found',
                status: 404,
              }),
              { status: 404 },
            );
          }
          return null;
        },
      },
    );
    await editor();
    await user.click(screen.getByTestId('editor-run'));
    await waitFor(() => expect(log.connections).toEqual(['project:parts-dev']));
    await waitFor(() =>
      expect(screen.getByTestId('result-status')).toHaveTextContent('3 rows · 25 ms'),
    );
    closed = true;
    await user.click(screen.getByTestId('editor-run'));
    await waitFor(() =>
      expect(log.connections).toEqual(['project:parts-dev', 'project:parts-dev']),
    );
  });
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const DIGEST_A = 'a'.repeat(64);
const DIGEST_B = 'b'.repeat(64);

/** A fake script folder of the project (STU-020): `GET /api/scripts`, `GET`/`PUT /api/scripts/file`. */
function scriptServer(files: Record<string, { content: string; digest: string }>) {
  const puts: Record<string, unknown>[] = [];
  const before = (url: string, init?: RequestInit): Response | null => {
    if (url === '/api/scripts') {
      return json({
        scripts: Object.entries(files).map(([path, file]) => ({ path, size: file.content.length })),
        truncated: false,
      });
    }
    if (url.startsWith('/api/scripts/file?')) {
      const path = new URLSearchParams(url.split('?')[1]).get('path') ?? '';
      const file = files[path];
      return file === undefined
        ? json({ code: 'studio.script_not_found', status: 404 }, 404)
        : json({ path, ...file });
    }
    if (url === '/api/scripts/file' && init?.method === 'PUT') {
      const body = JSON.parse(String(init.body)) as {
        path: string;
        content: string;
        digest: string | null;
        overwrite: boolean;
      };
      puts.push(body);
      const current = files[body.path];
      if (!body.overwrite && current !== undefined && body.digest === null) {
        return json({ code: 'studio.script_exists', status: 409 }, 409);
      }
      if (!body.overwrite && current !== undefined && body.digest !== current.digest) {
        return json({ code: 'studio.script_stale', status: 409 }, 409);
      }
      if (body.path.startsWith('missing/')) {
        return json({ code: 'studio.script_folder_missing', status: 404 }, 404);
      }
      files[body.path] = { content: body.content, digest: DIGEST_B };
      return json({ path: body.path, digest: DIGEST_B });
    }
    return null;
  };
  return { before, puts, files };
}

describe('EditorView: scripts, format and completion (STU-020)', () => {
  beforeEach(() => {
    completionClosed.count = 0;
    lastProps.current = null;
  });

  it('opens a project script in a new tab and saves it back, bound to the digest it read', async () => {
    const user = userEvent.setup();
    const scripts = scriptServer({
      'sql/report.sql': { content: 'select 1;\n', digest: DIGEST_A },
    });
    const { log } = setup({ sql: 'select 2' }, { before: scripts.before });
    await editor();
    await user.click(screen.getByTestId('editor-open'));
    const dialog = screen.getByRole('dialog', { name: 'Open script' });
    // The filter takes the first focus; the list names the scripts by their path in the project.
    await waitFor(() => expect(within(dialog).getByTestId('script-filter')).toHaveFocus());
    await user.click(await within(dialog).findByRole('button', { name: 'sql/report.sql' }));
    const text = await screen.findByRole('textbox', { name: 'SQL of report' });
    expect(text).toHaveValue('select 1;\n');
    expect(screen.getByTestId('editor-notice')).toHaveTextContent('sql/report.sql opened.');
    // Opening runs nothing.
    expect(log.starts).toEqual([]);
    fireEvent.change(text, { target: { value: 'select 10;\n' } });
    fireEvent.keyDown(text, { key: 's', ctrlKey: true });
    await waitFor(() => expect(scripts.puts).toHaveLength(1));
    expect(scripts.puts[0]).toEqual({
      path: 'sql/report.sql',
      content: 'select 10;\n',
      digest: DIGEST_A,
      overwrite: false,
    });
    await waitFor(() =>
      expect(screen.getByTestId('editor-notice')).toHaveTextContent('Saved to sql/report.sql.'),
    );
    expect(screen.getAllByTestId('editor-tab')[1]).not.toHaveTextContent('not saved');
    // The tab remembers its script (and the new digest) in the drafts.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 500));
    });
    expect(localStorage.getItem(DRAFTS_KEY)).toContain(`"script_digest":"${DIGEST_B}"`);
  });

  it('keeps the CRLF line endings of a script: the tab shows lines, the save writes CRLF back', async () => {
    const user = userEvent.setup();
    const scripts = scriptServer({
      'win.sql': { content: 'select 1;\r\nselect 2;\r\n', digest: DIGEST_A },
    });
    setup({ sql: 'select 0' }, { before: scripts.before });
    await editor();
    await user.click(screen.getByTestId('editor-open'));
    await user.click(await screen.findByRole('button', { name: 'win.sql' }));
    const text = await screen.findByRole('textbox', { name: 'SQL of win' });
    expect(text).toHaveValue('select 1;\nselect 2;\n');
    // Not changed by opening.
    expect(screen.getAllByTestId('editor-tab')[1]).not.toHaveTextContent('not saved');
    fireEvent.change(text, { target: { value: 'select 3;\nselect 4;\n' } });
    fireEvent.keyDown(text, { key: 's', ctrlKey: true });
    await waitFor(() => expect(scripts.puts).toHaveLength(1));
    expect(scripts.puts[0]).toMatchObject({
      content: 'select 3;\r\nselect 4;\r\n',
      digest: DIGEST_A,
    });
  });

  it('keeps mixed line endings of a script byte for byte: nothing is made uniform on save', async () => {
    const user = userEvent.setup();
    const mixed = 'select 1;\r\nselect 2;\nselect 3;\rselect 4;';
    const scripts = scriptServer({ 'mixed.sql': { content: mixed, digest: DIGEST_A } });
    setup({ sql: 'select 0' }, { before: scripts.before });
    await editor();
    await user.click(screen.getByTestId('editor-open'));
    await user.click(await screen.findByRole('button', { name: 'mixed.sql' }));
    const text = await screen.findByRole('textbox', { name: 'SQL of mixed' });
    expect(screen.getAllByTestId('editor-tab')[1]).not.toHaveTextContent('not saved');
    fireEvent.keyDown(text, { key: 's', ctrlKey: true });
    await waitFor(() => expect(scripts.puts).toHaveLength(1));
    expect(scripts.puts[0]).toMatchObject({ content: mixed, digest: DIGEST_A });
  });

  it('says how many links the script list left out', async () => {
    const user = userEvent.setup();
    const before = (url: string): Response | null =>
      url === '/api/scripts'
        ? json({ scripts: [{ path: 'a.sql', size: 1 }], truncated: false, skipped_links: 2 })
        : null;
    setup({ sql: 'select 0' }, { before });
    await editor();
    await user.click(screen.getByTestId('editor-open'));
    expect(await screen.findByTestId('script-skipped-links')).toHaveTextContent(
      '2 links were left out',
    );
  });

  it('shows a script name with a bidi control escaped in its tab', async () => {
    const user = userEvent.setup();
    const scripts = scriptServer({ 'a\u202Eb.sql': { content: 'select 1', digest: DIGEST_A } });
    setup({ sql: 'select 0' }, { before: scripts.before });
    await editor();
    await user.click(screen.getByTestId('editor-open'));
    await user.click(await screen.findByRole('button', { name: 'a\\u202Eb.sql' }));
    await waitFor(() =>
      expect(screen.getAllByTestId('editor-tab')[1]).toHaveTextContent('a\\u202Eb'),
    );
    expect(screen.getAllByTestId('editor-tab')[1]?.textContent).not.toContain('\u202E');
    // The editor's accessible name escapes it too.
    expect(await screen.findByRole('textbox', { name: 'SQL of a\\u202Eb' })).toBeInTheDocument();
  });

  it('asks for a path for a new script; a script that exists or changed is overwritten only on request', async () => {
    const user = userEvent.setup();
    const scripts = scriptServer({ 'taken.sql': { content: 'old', digest: DIGEST_A } });
    setup({ sql: 'select 3' }, { before: scripts.before });
    await editor();
    await user.click(screen.getByTestId('editor-save'));
    const dialog = screen.getByRole('dialog', { name: 'Save script' });
    const path = within(dialog).getByTestId('script-path');
    await waitFor(() => expect(path).toHaveFocus());
    expect(path).toHaveValue('Query 1.sql');
    // A path the server would refuse is refused here already.
    await user.clear(path);
    await user.type(path, '../outside.sql');
    await user.click(within(dialog).getByRole('button', { name: 'Save' }));
    expect(path).toHaveAttribute('aria-invalid', 'true');
    expect(scripts.puts).toEqual([]);
    await user.clear(path);
    await user.type(path, 'taken.sql');
    await user.click(within(dialog).getByRole('button', { name: 'Save' }));
    const overwrite = await screen.findByRole('dialog', { name: 'Overwrite script?' });
    expect(overwrite).toHaveTextContent('taken.sql already exists.');
    // The safe option has the focus (08 section 6).
    expect(within(overwrite).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    await user.click(within(overwrite).getByRole('button', { name: 'Overwrite' }));
    await waitFor(() => expect(scripts.puts).toHaveLength(2));
    expect(scripts.puts[1]).toMatchObject({ path: 'taken.sql', digest: null, overwrite: true });
    expect(await screen.findByRole('textbox', { name: 'SQL of taken' })).toHaveValue('select 3');
    expect(scripts.files['taken.sql']?.content).toBe('select 3');
  });

  it('shows why a save was refused in the save dialog, and keeps the download', async () => {
    const user = userEvent.setup();
    const scripts = scriptServer({});
    setup({ sql: 'select 4' }, { before: scripts.before });
    await editor();
    await user.click(screen.getByTestId('editor-save'));
    const path = screen.getByTestId('script-path');
    await user.clear(path);
    await user.type(path, 'missing/x.sql{Enter}');
    expect(await screen.findByTestId('script-save-problem')).toHaveTextContent(
      'The folder does not exist. Studio creates no folders.',
    );
    expect(screen.getByRole('button', { name: 'Download instead' })).toBeInTheDocument();
  });

  it("D-06's Save writes the script and then closes the tab", async () => {
    const user = userEvent.setup();
    const scripts = scriptServer({});
    setup({ sql: 'select 5' }, { before: scripts.before });
    await editor();
    await user.click(screen.getByRole('button', { name: 'Close Query 1' }));
    await user.click(
      within(screen.getByRole('dialog', { name: 'Script not saved' })).getByRole('button', {
        name: 'Save',
      }),
    );
    const path = await screen.findByTestId('script-path');
    await user.clear(path);
    await user.type(path, 'five.sql{Enter}');
    await waitFor(() => expect(scripts.files['five.sql']?.content).toBe('select 5'));
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'SQL of Query 1' })).toHaveValue(''),
    );
  });

  it('formats the text with :name placeholders kept, as one change; an incomplete text stays as it is', async () => {
    const user = userEvent.setup();
    setup({ sql: "select a, b from t where id = :id and note = ':no'" });
    const text = await editor();
    await user.click(screen.getByTestId('editor-format'));
    await waitFor(() =>
      expect(text).toHaveValue(
        "SELECT\n  a,\n  b\nFROM\n  t\nWHERE\n  id = :id\n  AND note = ':no'",
      ),
    );
    expect(screen.getByTestId('editor-notice')).toHaveTextContent('Formatted.');
    fireEvent.change(text, { target: { value: 'select (((' } });
    fireEvent.keyDown(text, { key: 'F', code: 'KeyF', altKey: true, shiftKey: true });
    await waitFor(() =>
      expect(screen.getByTestId('editor-notice')).toHaveTextContent(
        'The statement could not be formatted',
      ),
    );
    expect(text).toHaveValue('select (((');
  });

  it('formats only the selection when there is one', async () => {
    const user = userEvent.setup();
    setup({ sql: 'select 1;\nselect x from y' });
    const text = (await editor()) as HTMLTextAreaElement;
    text.setSelectionRange(10, 26);
    await user.click(screen.getByTestId('editor-format'));
    await waitFor(() => expect(text).toHaveValue('select 1;\nSELECT\n  x\nFROM\n  y'));
    expect(screen.getByTestId('editor-notice')).toHaveTextContent('Selection formatted.');
  });

  it('closes the completion list when a missing parameter takes the focus (B-6)', async () => {
    const user = userEvent.setup();
    setup({ sql: 'select :wanted' });
    await editor();
    await user.click(screen.getByTestId('editor-run'));
    await waitFor(() => expect(screen.getByRole('textbox', { name: ':wanted' })).toHaveFocus());
    expect(completionClosed.count).toBe(1);
  });

  it('completes the names of the catalog once the connection is open, and columns after table.', async () => {
    const user = userEvent.setup();
    const asked: string[] = [];
    setup(
      { sql: 'select 1' },
      {
        before: (url) => {
          if (url.includes('/catalog')) {
            asked.push(url);
            return json({
              connection_id: 'c-project:parts-dev',
              engine: 'sqlite',
              schemas: ['main'],
              objects: [
                { schema: 'main', name: 'parts', kind: 'table' },
                { schema: 'main', name: 'parts_idx', kind: 'other' },
              ],
              built_at: 1,
              cached: false,
            });
          }
          if (url.includes('/table?')) {
            asked.push(url);
            return json({
              schema: 'main',
              name: 'parts',
              kind: 'table',
              columns: [{ name: 'id' }, { name: 'label' }],
            });
          }
          return null;
        },
      },
    );
    await editor();
    // No connection yet: no catalog is read for completion.
    expect(lastProps.current?.schema).toBeUndefined();
    await user.click(screen.getByTestId('editor-run'));
    await waitFor(() => expect(lastProps.current?.schema).toEqual({ main: { parts: [] } }));
    expect(asked).toEqual(['/api/connections/c-project%3Aparts-dev/catalog']);
    expect(lastProps.current?.defaultSchema).toBe('main');
    const source = lastProps.current?.completionSource;
    expect(source).toBeDefined();
    const state = EditorState.create({ doc: 'select Parts.la' });
    const result = (await source?.(
      new CompletionContext(state, state.doc.length, false),
    )) as CompletionResult | null;
    expect(result?.from).toBe('select Parts.'.length);
    expect(result?.options.map((option) => option.label)).toEqual(['id', 'label']);
    expect(asked[1]).toBe('/api/connections/c-project%3Aparts-dev/table?schema=main&name=parts');
  });

  it('on PROD asks only for a catalog already read, and reads it in full after the click', async () => {
    const user = userEvent.setup();
    const asked: string[] = [];
    setup(
      { sql: 'select 1', profile: 'user:measure-central' },
      {
        before: (url) => {
          if (!url.includes('/catalog')) return null;
          asked.push(url);
          return url.endsWith('cached_only=true')
            ? json({ code: 'studio.catalog_not_loaded', status: 404 }, 404)
            : json({
                connection_id: 'c-user:measure-central',
                engine: 'postgresql',
                schemas: ['public'],
                objects: [{ schema: 'public', name: 'measurement', kind: 'table' }],
                built_at: 1,
                cached: false,
              });
        },
      },
    );
    await editor();
    await user.click(screen.getByTestId('editor-run'));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: /Connect/ }),
    );
    const load = await screen.findByTestId('editor-load-names');
    expect(asked).toEqual(['/api/connections/c-user%3Ameasure-central/catalog?cached_only=true']);
    expect(lastProps.current?.schema).toBeUndefined();
    // No column is read on PROD by typing.
    expect(lastProps.current?.completionSource).toBeUndefined();
    await user.click(load);
    // Through the shared D-02 guard (this test has no page session that remembers the first confirmation).
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: /Connect/ }),
    );
    await waitFor(() => expect(lastProps.current?.schema).toEqual({ public: { measurement: [] } }));
    expect(asked[1]).toBe('/api/connections/c-user%3Ameasure-central/catalog');
    expect(screen.queryByTestId('editor-load-names')).toBeNull();
  });
});
