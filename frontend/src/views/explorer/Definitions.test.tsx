/**
 * STU-022 in the explorer: the DDL tab, the policies tab and SQLite's row count on request. Each panel reads one GET
 * route of the explorer's open connection and nothing else; every server text is escaped (U-1), the DDL keeps its
 * line breaks and is cut with a note, "Copy DDL" copies the exact text, the policies say whether they bind the
 * connection's role, a row count past the server's timeout says so, and an answer for another object or after the
 * panel is gone is never shown.
 */
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { explorerAnswer, HOSTILE_TABLE, PG_DDL, PG_POLICIES } from '../../harness/explorer';
import { parseTable, type Engine, type TableDetail } from '../../api/explorer';
import { DdlView, MAX_DDL_CHARS, PoliciesView, RowCount, shownDdl } from './Definitions';
import { TableView, type TabId } from './ObjectDetail';

type Call = { url: string; method: string };

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

/** The harness's explorer, with `answers` by URL first. */
function server(answers: Record<string, () => Response | Promise<Response>> = {}) {
  const calls: Call[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const call = { url: String(input), method: init?.method ?? 'GET' };
    calls.push(call);
    const answer = answers[call.url];
    if (answer) return answer();
    return explorerAnswer(call.url, call.method) ?? new Response(null, { status: 404 });
  });
  return { fetcher: fetcher as unknown as typeof fetch, calls };
}

function later() {
  let send: (response: Response) => void = () => undefined;
  const promise = new Promise<Response>((resolve) => {
    send = resolve;
  });
  return { answer: () => promise, send };
}

const route = (connection: string, name: string, schema: string, object: string) =>
  `/api/connections/${connection}/${name}?${new URLSearchParams({ schema, name: object }).toString()}`;

const BIDI = String.fromCodePoint(0x202e);

describe('the DDL tab', () => {
  it('reads the DDL on the given connection with one GET and shows it with its owner', async () => {
    const { fetcher, calls } = server();
    render(<DdlView fetcher={fetcher} connectionId="c-pg" schema="public" name="measurement" />);
    expect(screen.getByText('Reading the DDL…')).toBeVisible();
    const text = await screen.findByTestId('ddl-text');
    expect(calls).toEqual([{ url: route('c-pg', 'ddl', 'public', 'measurement'), method: 'GET' }]);
    // The line breaks stay; the region scrolls and takes the focus (axe scrollable-region-focusable).
    expect(text.textContent).toContain(
      'CREATE TABLE public.measurement (\n    id bigint NOT NULL,',
    );
    expect(text.textContent).toContain('ENABLE ROW LEVEL SECURITY;\n\nCREATE POLICY');
    expect(text).toHaveAttribute('tabindex', '0');
    expect(text).toHaveAccessibleName('DDL of public.measurement');
    expect(screen.getByTestId('ddl-owner')).toHaveTextContent('Owner: measure_owner');
  });

  it('escapes every control and bidi character of the DDL but its line breaks (U-1)', async () => {
    const { fetcher } = server();
    render(
      <DdlView fetcher={fetcher} connectionId="c-sqlite" schema="main" name={HOSTILE_TABLE} />,
    );
    const text = (await screen.findByTestId('ddl-text')).textContent ?? '';
    expect(text).not.toContain(BIDI);
    expect(text).not.toContain('\r');
    expect(text).toContain(
      // A line break in the text stays one (it is part of the DDL); everything else is an escape.
      '"notes\\u202Etxt.exe\nx" (body\\u202Eevil TEXT) -- \\\\ \\u000D<b>bold</b>',
    );
    // Markup in the DDL is text, never HTML.
    expect(screen.getByTestId('ddl-text').querySelector('b')).toBeNull();
    // The accessible name escapes the object's name too.
    expect(screen.getByTestId('ddl-text').getAttribute('aria-label')).not.toContain(BIDI);
  });

  it('cuts a very long DDL with a note; the shown part is escaped per line', () => {
    const long = `${'x'.repeat(MAX_DDL_CHARS)}\n${BIDI}`;
    expect(shownDdl(long)).toEqual({ text: 'x'.repeat(MAX_DDL_CHARS), cut: true });
    // Tabs and line breaks stay (I-1 of stu-022-page-review.md); other controls are escapes.
    expect(shownDdl(`a\n${BIDI}\tb\r`)).toEqual({ text: 'a\n\\u202E\tb\\u000D', cut: false });
  });

  it('shows the cut note for a DDL past the limit, and Copy DDL copies all of it', async () => {
    const statement = `CREATE VIEW v AS SELECT '${'y'.repeat(MAX_DDL_CHARS)}'`;
    const body = {
      ...(PG_DDL.device as object),
      statements: [statement],
      ddl: `${statement};\n\n`,
    };
    const { fetcher } = server({ [route('c-pg', 'ddl', 'public', 'device')]: () => json(body) });
    const writeText = vi.fn(async () => undefined);
    // After userEvent.setup(), which puts a clipboard stub of its own on navigator.
    const user = userEvent.setup();
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    render(<DdlView fetcher={fetcher} connectionId="c-pg" schema="public" name="device" />);
    expect(await screen.findByTestId('ddl-cut')).toHaveTextContent(
      'The DDL is longer than 100,000 characters',
    );
    await user.click(screen.getByRole('button', { name: 'Copy DDL' }));
    expect(writeText).toHaveBeenCalledWith(`${statement};\n\n`);
    const status = screen.getByTestId('ddl-copy-status');
    await waitFor(() => expect(status).toHaveTextContent('The DDL is on the clipboard.'));
    // A second copy empties the status first, so the same text is announced again (I-4).
    await user.click(screen.getByRole('button', { name: 'Copy DDL' }));
    expect(status).toHaveTextContent('');
    await waitFor(() => expect(status).toHaveTextContent('The DDL is on the clipboard.'));
    expect(writeText).toHaveBeenCalledTimes(2);
  });

  it('says so when the clipboard refuses the copy', async () => {
    const { fetcher } = server();
    const user = userEvent.setup();
    const writeText = vi.fn(async () => {
      throw new Error('denied');
    });
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    render(<DdlView fetcher={fetcher} connectionId="c-pg" schema="public" name="device" />);
    await user.click(await screen.findByRole('button', { name: 'Copy DDL' }));
    await waitFor(() =>
      expect(screen.getByTestId('ddl-copy-status')).toHaveTextContent('could not copy the DDL'),
    );
  });

  it('an object without statements says so and offers no copy', async () => {
    const body = {
      ...(PG_DDL.device as object),
      statements: [],
      ddl: '',
      notes: ["The identity sequence of id: its DDL is part of the table's."],
    };
    const { fetcher } = server({ [route('c-pg', 'ddl', 'public', 'device')]: () => json(body) });
    render(<DdlView fetcher={fetcher} connectionId="c-pg" schema="public" name="device" />);
    expect(await screen.findByTestId('ddl-empty')).toBeVisible();
    expect(screen.getByTestId('ddl-notes')).toHaveTextContent('The identity sequence of id');
    expect(screen.queryByRole('button', { name: 'Copy DDL' })).toBeNull();
  });

  it('a DDL for another object or connection is refused (fail closed, I-1)', async () => {
    for (const body of [
      { ...(PG_DDL.device as object), name: 'measurement' },
      { ...(PG_DDL.device as object), connection_id: 'c-other' },
      { ...(PG_DDL.device as object), statements: ['CREATE TABLE x ()', 7] },
    ]) {
      const { fetcher } = server({ [route('c-pg', 'ddl', 'public', 'device')]: () => json(body) });
      const { unmount } = render(
        <DdlView fetcher={fetcher} connectionId="c-pg" schema="public" name="device" />,
      );
      expect(await screen.findByTestId('definition-problem')).toHaveTextContent(
        "The server's answer could not be read",
      );
      expect(screen.queryByTestId('ddl-text')).toBeNull();
      unmount();
    }
  });

  it('a problem of the server is shown in the page’s own words', async () => {
    const { fetcher } = server({
      [route('c-pg', 'ddl', 'public', 'device')]: () =>
        json({ code: 'studio.object_not_found', kind: 'not_found', status: 404 }, 404),
    });
    render(<DdlView fetcher={fetcher} connectionId="c-pg" schema="public" name="device" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('no longer in the database');
  });

  it('an answer that arrives after the panel is gone is dropped', async () => {
    const slow = later();
    const { fetcher } = server({ [route('c-pg', 'ddl', 'public', 'device')]: slow.answer });
    const errors = vi.spyOn(console, 'error');
    const { unmount } = render(
      <DdlView fetcher={fetcher} connectionId="c-pg" schema="public" name="device" />,
    );
    unmount();
    await act(async () => {
      slow.send(json(PG_DDL.device));
      await Promise.resolve();
    });
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });
});

describe('the policies tab', () => {
  it('RLS on, the policies bind the role: facts, reason and every policy, escaped', async () => {
    const { fetcher, calls } = server();
    render(
      <PoliciesView fetcher={fetcher} connectionId="c-pg" schema="public" name="measurement" />,
    );
    expect(screen.getByText('Reading the row-level security…')).toBeVisible();
    const items = await screen.findAllByTestId('detail-policy');
    expect(calls).toEqual([
      { url: route('c-pg', 'policies', 'public', 'measurement'), method: 'GET' },
    ]);
    expect(screen.getByTestId('policies-facts')).toHaveTextContent(
      'Row-level securityOnForced for the ownerNoOwnermeasure_ownerYour roleanalyst',
    );
    expect(screen.getByTestId('policies-applies')).toHaveTextContent(
      'The policies bind your role analyst: it sees only the rows they allow. If no permissive policy applies to your role, directly or through a role it belongs to, it sees no rows.',
    );
    expect(screen.queryByTestId('policies-deny')).toBeNull();
    expect(items.map((item) => item.textContent)).toEqual([
      'own_devicesPermissiveSELECTanalyst(device_id IN ( SELECT device.id\\u000A   FROM device))',
      'no\\u202E_writesRestrictiveALLAll roles (PUBLIC)truefalse',
    ]);
    expect(screen.getByRole('region', { name: 'Policies' })).toHaveAttribute('tabindex', '0');
  });

  it('RLS on without policies: the owner is not bound, and everyone bound sees no rows', async () => {
    const { fetcher } = server();
    render(<PoliciesView fetcher={fetcher} connectionId="c-pg" schema="public" name="device" />);
    expect(await screen.findByTestId('policies-applies')).toHaveTextContent(
      'Your role measure_owner owns the table, or is a member of the role that owns it, and the security is not forced',
    );
    expect(screen.getByTestId('policies-deny')).toHaveTextContent(
      'every role it binds sees no rows',
    );
    expect(screen.getByText('This table has no policies.')).toBeVisible();
  });

  it('RLS off without policies: no claim that RLS is on or that rows are denied (L-3)', async () => {
    const body = { ...(PG_POLICIES.device as object), rls_enabled: false };
    const { fetcher } = server({
      [route('c-pg', 'policies', 'public', 'device')]: () => json(body),
    });
    render(<PoliciesView fetcher={fetcher} connectionId="c-pg" schema="public" name="device" />);
    expect(await screen.findByTestId('policies-applies')).toHaveTextContent(
      'Row-level security is off',
    );
    expect(screen.getByTestId('policies-rls')).toHaveTextContent('Off');
    expect(screen.queryByTestId('policies-deny')).toBeNull();
    expect(screen.getByText('This table has no policies.')).toBeVisible();
  });

  it.each([
    [{ rls_enabled: false }, 'Row-level security is off: the policies bind no one.'],
    [
      { current_role: { name: 'admin', superuser: true, bypass_rls: true, owner: false } },
      'Your role admin is a superuser',
    ],
    [
      { current_role: { name: 'etl', superuser: false, bypass_rls: true, owner: false } },
      'Your role etl has BYPASSRLS',
    ],
    [
      {
        rls_forced: true,
        rls_applies: true,
        current_role: { name: 'measure_owner', superuser: false, bypass_rls: false, owner: true },
      },
      'The policies bind your role measure_owner',
    ],
  ])('the reason the policies do or do not bind the role: %j', async (change, reason) => {
    const body = { ...(PG_POLICIES.measurement as object), rls_applies: false, ...change };
    const { fetcher } = server({
      [route('c-pg', 'policies', 'public', 'measurement')]: () => json(body),
    });
    render(
      <PoliciesView fetcher={fetcher} connectionId="c-pg" schema="public" name="measurement" />,
    );
    expect(await screen.findByTestId('policies-applies')).toHaveTextContent(reason);
  });

  it('a policy the page cannot read in full refuses the whole answer (fail closed)', async () => {
    const good = PG_POLICIES.measurement as { policies: object[] };
    for (const policy of [
      { ...good.policies[0], command: 'TRUNCATE' },
      { ...good.policies[0], roles: 'analyst' },
      { ...good.policies[0], permissive: 'yes' },
    ]) {
      const body = { ...good, policies: [policy] };
      const { fetcher } = server({
        [route('c-pg', 'policies', 'public', 'measurement')]: () => json(body),
      });
      const { unmount } = render(
        <PoliciesView fetcher={fetcher} connectionId="c-pg" schema="public" name="measurement" />,
      );
      expect(await screen.findByTestId('definition-problem')).toBeVisible();
      expect(screen.queryByTestId('detail-policy')).toBeNull();
      unmount();
    }
  });
});

describe('SQLite row count on request', () => {
  it('counts only on request, with one GET on the given connection', async () => {
    const { fetcher, calls } = server();
    const user = userEvent.setup();
    render(<RowCount fetcher={fetcher} connectionId="c-sqlite" schema="main" name="part" />);
    expect(calls).toEqual([]);
    await user.click(screen.getByRole('button', { name: 'Count rows' }));
    await waitFor(() =>
      expect(screen.getByTestId('row-count-result')).toHaveTextContent(
        '1,234 rows (counted in 3 ms).',
      ),
    );
    expect(calls).toEqual([{ url: route('c-sqlite', 'row-count', 'main', 'part'), method: 'GET' }]);
  });

  it('a count past the server timeout says so', async () => {
    const { fetcher } = server();
    const user = userEvent.setup();
    render(<RowCount fetcher={fetcher} connectionId="c-sqlite" schema="main" name="material" />);
    await user.click(screen.getByRole('button', { name: 'Count rows' }));
    await waitFor(() =>
      expect(screen.getByTestId('row-count-result')).toHaveTextContent(
        'Counting took longer than 10 seconds',
      ),
    );
  });

  it('a second click while counting sends nothing; the button keeps the focus', async () => {
    const slow = later();
    const { fetcher, calls } = server({
      [route('c-sqlite', 'row-count', 'main', 'part')]: slow.answer,
    });
    const user = userEvent.setup();
    render(<RowCount fetcher={fetcher} connectionId="c-sqlite" schema="main" name="part" />);
    const button = screen.getByRole('button', { name: 'Count rows' });
    await user.click(button);
    expect(button).toHaveAttribute('aria-disabled', 'true');
    await user.click(button);
    expect(calls).toHaveLength(1);
    expect(button).toHaveFocus();
    await act(async () => {
      slow.send(
        json({
          connection_id: 'c-sqlite',
          schema: 'main',
          name: 'part',
          count: 1,
          elapsed_ms: null,
        }),
      );
      await Promise.resolve();
    });
    expect(screen.getByTestId('row-count-result')).toHaveTextContent('1 row (counted).');
  });

  it('a count for another object is refused (I-1)', async () => {
    const { fetcher } = server({
      [route('c-sqlite', 'row-count', 'main', 'part')]: () =>
        json({ connection_id: 'c-sqlite', schema: 'main', name: 'material', count: 5 }),
    });
    const user = userEvent.setup();
    render(<RowCount fetcher={fetcher} connectionId="c-sqlite" schema="main" name="part" />);
    await user.click(screen.getByRole('button', { name: 'Count rows' }));
    await waitFor(() =>
      expect(screen.getByTestId('row-count-result')).toHaveTextContent(
        "The server's answer could not be read",
      ),
    );
  });
});

describe('one panel per connection and object (L-2)', () => {
  // Two objects whose names would join to the same text with a separator: `a|b`.`c` and `a`.`b|c`.
  const detail = (schema: string, name: string): TableDetail => {
    const value = parseTable({
      connection_id: 'c-x',
      schema,
      name,
      kind: 'table',
      columns: [{ name: 'id', type: 'INTEGER', nullable: false, default: null, comment: null }],
      primary_key: null,
      foreign_keys: [],
      referenced_by: [],
      unique_constraints: [],
      check_constraints: [],
      indexes: [],
      row_estimate: null,
    });
    if (value === null) throw new Error('bad detail');
    return value;
  };
  const first = detail('a|b', 'c');
  const second = detail('a', 'b|c');
  const ddlOf = (schema: string, name: string) => {
    const statement = `CREATE TABLE "${schema}"."${name}" (id INTEGER)`;
    return json({
      connection_id: 'c-x',
      schema,
      name,
      statements: [statement],
      ddl: `${statement};\n\n`,
    });
  };
  const countOf = (schema: string, name: string, rows: number) =>
    json({ connection_id: 'c-x', schema, name, count: rows, elapsed_ms: 1 });

  function view(fetcher: typeof fetch, shown: TableDetail, tab: TabId, engine: Engine) {
    return (
      <TableView
        detail={shown}
        engine={engine}
        sqliteInfo={null}
        tab={tab}
        onTab={() => undefined}
        find={() => null}
        onFollow={() => undefined}
        fetcher={fetcher}
        connectionId="c-x"
        onGone={() => undefined}
      />
    );
  }

  it('the DDL tab reads the next object again when the view shows another one', async () => {
    const { fetcher, calls } = server({
      [route('c-x', 'ddl', 'a|b', 'c')]: () => ddlOf('a|b', 'c'),
      [route('c-x', 'ddl', 'a', 'b|c')]: () => ddlOf('a', 'b|c'),
    });
    const { rerender } = render(view(fetcher, first, 'ddl', 'postgresql'));
    expect(await screen.findByTestId('ddl-text')).toHaveTextContent('CREATE TABLE "a|b"."c"');
    rerender(view(fetcher, second, 'ddl', 'postgresql'));
    await waitFor(() =>
      expect(screen.getByTestId('ddl-text')).toHaveTextContent('CREATE TABLE "a"."b|c"'),
    );
    expect(calls.map((call) => call.url)).toEqual([
      route('c-x', 'ddl', 'a|b', 'c'),
      route('c-x', 'ddl', 'a', 'b|c'),
    ]);
  });

  it('the row count of one object is not shown for the next', async () => {
    const { fetcher } = server({
      [route('c-x', 'row-count', 'a|b', 'c')]: () => countOf('a|b', 'c', 7),
    });
    const user = userEvent.setup();
    const { rerender } = render(view(fetcher, first, 'columns', 'sqlite'));
    await user.click(screen.getByRole('button', { name: 'Count rows' }));
    await waitFor(() => expect(screen.getByTestId('row-count-result')).toHaveTextContent('7 rows'));
    rerender(view(fetcher, second, 'columns', 'sqlite'));
    expect(screen.getByTestId('row-count-result')).toHaveTextContent('');
  });
});
