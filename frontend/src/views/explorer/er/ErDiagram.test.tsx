/** STU-023: the ER diagram view in jsdom (its drawing and keyboard in a browser: e2e/er-diagram.spec.ts). */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { parseCatalog, type Catalog } from '../../../api/explorer';
import { PG_CATALOG, explorerAnswer, largeDiagram } from '../../../harness/explorer';
import { ErDiagram } from './ErDiagram';

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

const catalog = parseCatalog(PG_CATALOG) as Catalog;

function server(options: { large?: number; status?: number; code?: string } = {}) {
  const asked: string[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    asked.push(`${init?.method ?? 'GET'} ${url}`);
    if (options.code !== undefined) {
      return new Response(JSON.stringify({ code: options.code, status: options.status }), {
        status: options.status ?? 404,
      });
    }
    if (options.large !== undefined && url.includes('schema=audit')) {
      return new Response(JSON.stringify(largeDiagram(options.large)), { status: 200 });
    }
    return explorerAnswer(url, init?.method ?? 'GET') ?? new Response('{}', { status: 404 });
  }) as unknown as typeof fetch;
  return { fetcher, asked };
}

function view(fetcher: typeof fetch, extra: Partial<Parameters<typeof ErDiagram>[0]> = {}) {
  const onShow = vi.fn();
  const onGone = vi.fn();
  const save = vi.fn();
  render(
    <ErDiagram
      fetcher={fetcher}
      connectionId="c-pg"
      catalog={catalog}
      onShow={onShow}
      onGone={onGone}
      save={save}
      {...extra}
    />,
  );
  return { onShow, onGone, save };
}

describe('ErDiagram', () => {
  it('reads the schema public once and lists its tables and keys', async () => {
    const { fetcher, asked } = server();
    view(fetcher);
    await waitFor(() =>
      expect(screen.getByTestId('er-status')).toHaveTextContent(
        /^Tables: 3, relationships: 1, layout: \d+ ms\.$/,
      ),
    );
    expect(asked).toEqual(['GET /api/connections/c-pg/diagram?schema=public']);
    const rows = screen.getAllByTestId('er-list-row');
    expect(rows).toHaveLength(3);
    const measurement = rows.find((row) => row.textContent?.startsWith('measurement'));
    expect(measurement).toHaveTextContent('measurement_device_id_fkey: (device_id) → device (id)');
    const device = rows.find((row) => row.textContent?.startsWith('device'));
    expect(device).toHaveTextContent('measurement (device_id), measurement_device_id_fkey');
    expect(
      screen.getByRole('group', { name: /^ER diagram of public with 3 tables/ }),
    ).toBeVisible();
  });

  it('shows a table in the explorer from the table alternative', async () => {
    const { fetcher } = server();
    const { onShow } = view(fetcher);
    const list = await screen.findByTestId('er-list');
    await userEvent.click(
      within(list).getByRole('button', { name: 'Show device in the explorer' }),
    );
    expect(onShow).toHaveBeenCalledWith({ schema: 'public', name: 'device', kind: 'table' });
  });

  it('reads another schema when it is picked', async () => {
    const { fetcher, asked } = server({ large: 12 });
    view(fetcher);
    await screen.findByTestId('er-list');
    await userEvent.selectOptions(screen.getByTestId('er-schema'), 'audit');
    await waitFor(() => expect(screen.getAllByTestId('er-list-row')).toHaveLength(12));
    expect(asked).toEqual([
      'GET /api/connections/c-pg/diagram?schema=public',
      'GET /api/connections/c-pg/diagram?schema=audit',
    ]);
  });

  it('shows the first 150 of a larger schema, says so, and applies a new choice with one button', async () => {
    const { fetcher } = server({ large: 160 });
    view(fetcher);
    await userEvent.selectOptions(await screen.findByTestId('er-schema'), 'audit');
    await waitFor(() => expect(screen.getAllByTestId('er-list-row')).toHaveLength(150));
    expect(screen.getByTestId('er-capped')).toHaveTextContent(
      'The schema has 160 tables; one diagram shows at most 150.',
    );
    const choose = screen.getByTestId('er-choose');
    // A button opens the choice (not a <details>, which WebKit's CSP refuses, TD-052); closed, it renders no rows.
    expect(choose.querySelector('details')).toBeNull();
    const toggle = within(choose).getByRole('button', { name: 'Tables: 150 of 160' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(within(choose).queryByRole('checkbox')).toBeNull();
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(document.getElementById(toggle.getAttribute('aria-controls') ?? '')).toContainElement(
      within(choose).getByTestId('er-choose-filter'),
    );
    // The choice is full: the tables not chosen cannot be added.
    expect(within(choose).getByRole('checkbox', { name: 'audit_155' })).toBeDisabled();
    expect(within(choose).getByText('At most 150 tables fit in one diagram.')).toBeInTheDocument();
    await userEvent.click(within(choose).getByTestId('er-choose-none'));
    expect(screen.getAllByTestId('er-list-row')).toHaveLength(150); // nothing applied yet
    const apply = within(choose).getByTestId('er-choose-apply');
    expect(apply).toHaveAttribute('aria-disabled', 'true');
    await userEvent.click(apply);
    expect(screen.getAllByTestId('er-list-row')).toHaveLength(150);
    fireEvent.change(within(choose).getByTestId('er-choose-filter'), { target: { value: '_15' } });
    for (const name of ['audit_150', 'audit_155', 'audit_159']) {
      await userEvent.click(within(choose).getByRole('checkbox', { name }));
    }
    expect(apply).toHaveTextContent('Show 3 tables');
    await userEvent.click(apply);
    await waitFor(() => expect(screen.getAllByTestId('er-list-row')).toHaveLength(3));
    expect(within(choose).getByText('Tables: 3 of 160')).toBeInTheDocument();
  });

  it('saves the shown scene as SVG under the schema name', async () => {
    const { fetcher } = server();
    const { save } = view(fetcher);
    await screen.findByTestId('er-list');
    await userEvent.click(screen.getByTestId('er-svg'));
    expect(save).toHaveBeenCalledTimes(1);
    const [blob, name] = save.mock.calls[0] as [Blob, string];
    expect(name).toBe('er-diagram-public.svg');
    expect(blob.type).toBe('image/svg+xml');
    const text = await blob.text();
    expect(text).toContain('>measurement<');
    expect(text.match(/<g transform=/g)).toHaveLength(3);
    expect(screen.getByTestId('er-status')).toHaveTextContent('Saved er-diagram-public.svg.');
  });

  it('says so when the browser cannot draw the PNG, and saves nothing', async () => {
    const { fetcher } = server();
    const { save } = view(fetcher);
    await screen.findByTestId('er-list');
    const getContext = vi
      .spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockImplementation(() => null);
    await userEvent.click(screen.getByTestId('er-png'));
    expect(await screen.findByTestId('er-export-problem')).toHaveTextContent(
      'The browser could not draw the PNG. Save the diagram as SVG.',
    );
    expect(save).not.toHaveBeenCalled();
    getContext.mockRestore();
  });

  it('shows a refused answer, and hands a connection that is gone to the explorer', async () => {
    const tooLarge = server({ code: 'studio.diagram_too_large', status: 422 });
    const first = view(tooLarge.fetcher);
    expect(await screen.findByTestId('er-problem')).toHaveTextContent(
      'The schema has more than 1000 tables',
    );
    expect(first.onGone).not.toHaveBeenCalled();
    act(() => document.body.replaceChildren());
    const gone = server({ code: 'studio.connection_not_found', status: 404 });
    const second = view(gone.fetcher);
    await screen.findByTestId('er-problem');
    expect(second.onGone).toHaveBeenCalledWith(
      expect.objectContaining({ code: 'studio.connection_not_found' }),
    );
  });

  it('refuses an answer the page cannot read (fail closed)', async () => {
    const fetcher = (async () =>
      new Response(JSON.stringify({ schema: 'public', engine: 'postgresql', tables: [{}] }), {
        status: 200,
      })) as unknown as typeof fetch;
    view(fetcher);
    expect(await screen.findByTestId('er-problem')).toBeInTheDocument();
    expect(screen.queryByTestId('er-list')).toBeNull();
  });
});
