/**
 * TD-043: the export dialog with the fake export route of the harness (`harness/queries.ts`). Format, rows (the
 * fetched rows or a limit), the CSV formula option, the limit check, progress, cancel by button and by Escape (the
 * request is aborted and nothing is saved), closing and leaving abort too, a double press starts one export, the cut
 * hint, refusals in the page's words, German, and the focus rules of the shared dialog.
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import { createQueryServer } from '../../harness/queries';
import i18n from '../../i18n';
import type { SaveFile } from '../../lib/download';
import { ExportDialog, formatBytes, parseRowLimit, type ExportDialogProps } from './ExportDialog';

function setup(overrides: Partial<ExportDialogProps> = {}) {
  const server = createQueryServer();
  const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) =>
    (await server.handle(String(input), init)) ??
    new Response(null, { status: 404 })) as typeof fetch;
  const save = vi.fn<SaveFile>();
  const onDone = vi.fn<(message: string) => void>();
  const onClose = vi.fn();
  const view = render(
    <ExportDialog
      fetcher={fetcher}
      connectionId="c-project:parts-dev"
      source={{
        kind: 'sql',
        sql: 'select * from generate_series(1, 5) where n > :min',
        params: { min: 0 },
      }}
      subject="result 1 of Query 1"
      fetchedRows={5}
      save={save}
      onDone={onDone}
      onClose={onClose}
      {...overrides}
    />,
  );
  return { log: server.log, save, onDone, onClose, view };
}

const dialog = () => screen.getByRole('dialog', { name: 'Export' });

afterEach(async () => {
  await act(async () => {
    await i18n.changeLanguage('en');
  });
});

describe('ExportDialog', () => {
  it('opens on Cancel, says what it exports, and exports the fetched rows as CSV by default', async () => {
    const user = userEvent.setup();
    const { log, save, onDone } = setup();
    expect(dialog()).toHaveAccessibleDescription(
      'Export result 1 of Query 1 as a file. The export only reads.',
    );
    expect(within(dialog()).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    expect(within(dialog()).getByRole('radio', { name: 'CSV (for spreadsheets)' })).toBeChecked();
    expect(
      within(dialog()).getByRole('radio', { name: 'The fetched rows of the result (5)' }),
    ).toBeChecked();
    expect(within(dialog()).getByRole('checkbox', { name: /spreadsheet formulas/ })).toBeChecked();
    expect(screen.getByTestId('export-note')).toHaveTextContent(
      'Studio runs the statement again for the export, read-only',
    );
    await user.click(within(dialog()).getByRole('button', { name: 'Export' }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(log.exports).toEqual([
      {
        connectionId: 'c-project:parts-dev',
        format: 'csv',
        sql: 'select * from generate_series(1, 5) where n > :min',
        params: { min: 0 },
        maxRows: 5,
        escapeFormulas: true,
        aborted: false,
      },
    ]);
    const [blob, name] = save.mock.calls[0] ?? [];
    expect(name).toBe('export.csv');
    const text = (await blob?.text()) ?? '';
    expect(text.split('\r\n')[0]).toBe('n,label,note,param');
    // The server's formula protection is on: the formula cell comes with a leading quote.
    expect(text).toContain("'=SUM(A1:A2)");
    expect(onDone).toHaveBeenCalledWith(
      expect.stringMatching(/^Export saved: export\.csv \(\d+ B\)\.$/),
    );
  });

  it('exports JSON up to a typed limit, without the formula option, and says when the file is cut', async () => {
    const user = userEvent.setup();
    const { log, save, onDone } = setup({
      source: { kind: 'sql', sql: 'select * from generate_series(1, 50)', params: {} },
      fetchedRows: 10,
      capped: true,
    });
    expect(screen.getByTestId('export-note')).toHaveTextContent(
      'The result was cut, so this export stops at the same number of rows.',
    );
    await user.click(within(dialog()).getByRole('radio', { name: 'JSON (for programs)' }));
    expect(within(dialog()).queryByRole('checkbox')).toBeNull();
    const limit = within(dialog()).getByRole('textbox', { name: 'Row limit' });
    await user.clear(limit);
    await user.type(limit, '20');
    expect(within(dialog()).getByRole('radio', { name: 'Up to' })).toBeChecked();
    expect(screen.getByTestId('export-note')).toHaveTextContent(
      'If there are more rows than the limit, the file stops there and says so.',
    );
    await user.click(within(dialog()).getByRole('button', { name: 'Export' }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(log.exports[0]).toMatchObject({ format: 'json', maxRows: 20, escapeFormulas: null });
    expect(save.mock.calls[0]?.[1]).toBe('export.json');
    const doc = JSON.parse((await save.mock.calls[0]?.[0].text()) ?? '') as {
      rows: unknown[];
      truncated: string | null;
    };
    expect(doc.rows).toHaveLength(20);
    expect(doc.truncated).toBe('rows');
    expect(onDone).toHaveBeenCalledWith(
      expect.stringMatching(/It stops at 20 rows; more rows exist\.$/),
    );
  });

  it('turns the CSV formula option off when asked', async () => {
    const user = userEvent.setup();
    const { log, save } = setup();
    await user.click(within(dialog()).getByRole('checkbox', { name: /spreadsheet formulas/ }));
    await user.click(within(dialog()).getByRole('button', { name: 'Export' }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(log.exports[0]?.escapeFormulas).toBe(false);
    expect(await save.mock.calls[0]?.[0].text()).toContain('\r\n1,row 1,=SUM(A1:A2),0\r\n');
  });

  it('refuses a limit outside 1 to 1 000 000: the field says so, gets the focus, and nothing is sent', async () => {
    const user = userEvent.setup();
    const { log } = setup({ fetchedRows: null });
    const limit = within(dialog()).getByRole('textbox', { name: 'Row limit' });
    // A table: nothing fetched, so "Up to" is the only choice.
    expect(within(dialog()).queryByRole('radio', { name: /fetched/ })).toBeNull();
    for (const bad of ['0', '1000001', '1.5', '-3', 'abc', '']) {
      await user.clear(limit);
      if (bad !== '') await user.type(limit, bad);
      await user.click(within(dialog()).getByRole('button', { name: 'Export' }));
      expect(limit).toHaveAttribute('aria-invalid', 'true');
      expect(limit).toHaveAccessibleDescription('Enter a whole number from 1 to 1,000,000.');
      expect(limit).toHaveFocus();
    }
    expect(log.exports).toEqual([]);
    await user.clear(limit);
    await user.type(limit, '1000000');
    expect(limit).not.toHaveAttribute('aria-invalid');
  });

  it('Cancel aborts a running export: the request goes, nothing is saved, and it can run again', async () => {
    const user = userEvent.setup();
    const { log, save, onClose } = setup({
      source: { kind: 'sql', sql: 'select export_slow', params: {} },
    });
    const start = within(dialog()).getByRole('button', { name: 'Export' });
    await user.click(start);
    await waitFor(() =>
      expect(screen.getByTestId('export-progress')).toHaveTextContent(
        /^Exporting… \d+ B received$/,
      ),
    );
    expect(start).toHaveAttribute('aria-disabled', 'true');
    expect(start).toHaveFocus();
    await user.click(within(dialog()).getByRole('button', { name: 'Cancel export' }));
    await waitFor(() =>
      expect(screen.getByTestId('export-progress')).toHaveTextContent(
        'The export was canceled; nothing was saved.',
      ),
    );
    expect(log.exports[0]?.aborted).toBe(true);
    expect(save).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(start).not.toHaveAttribute('aria-disabled');
    expect(within(dialog()).getByRole('button', { name: 'Cancel' })).toBeVisible();
  });

  it('Escape aborts a running export first, and closes the dialog when nothing runs', async () => {
    const user = userEvent.setup();
    const { log, onClose } = setup({
      source: { kind: 'sql', sql: 'select export_slow', params: {} },
    });
    await user.click(within(dialog()).getByRole('button', { name: 'Export' }));
    await waitFor(() => expect(log.exports).toHaveLength(1));
    await user.keyboard('{Escape}');
    await waitFor(() => expect(log.exports[0]?.aborted).toBe(true));
    expect(onClose).not.toHaveBeenCalled();
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('leaving (the dialog unmounts) aborts a running export', async () => {
    const user = userEvent.setup();
    const { log, view, save } = setup({
      source: { kind: 'sql', sql: 'select export_slow', params: {} },
    });
    await user.click(within(dialog()).getByRole('button', { name: 'Export' }));
    await waitFor(() => expect(log.exports).toHaveLength(1));
    view.unmount();
    await waitFor(() => expect(log.exports[0]?.aborted).toBe(true));
    expect(save).not.toHaveBeenCalled();
  });

  it('two presses before the next render start one export', async () => {
    const { log, save } = setup();
    const start = within(dialog()).getByRole('button', { name: 'Export' });
    act(() => {
      start.click();
      start.click();
    });
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(log.exports).toHaveLength(1);
  });

  it("shows a refusal in the page's words, or the caller's, and saves nothing", async () => {
    const user = userEvent.setup();
    const busy = setup({ source: { kind: 'sql', sql: 'select export_busy', params: {} } });
    await user.click(within(dialog()).getByRole('button', { name: 'Export' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Another export is running on this connection, or two are running already.',
    );
    expect(busy.save).not.toHaveBeenCalled();
    busy.view.unmount();

    const own = setup({
      source: { kind: 'sql', sql: 'delete from parts', params: {} },
      describeProblem: (_t, problem) => `own text for ${problem.code}`,
    });
    await user.click(within(dialog()).getByRole('button', { name: 'Export' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'own text for studio.statement_refused',
    );
    expect(own.save).not.toHaveBeenCalled();
  });

  it('speaks German', async () => {
    await act(async () => {
      await i18n.changeLanguage('de');
    });
    setup();
    const de = screen.getByRole('dialog', { name: 'Exportieren' });
    expect(de).toHaveAccessibleDescription(
      'result 1 of Query 1 als Datei exportieren. Der Export liest nur.',
    );
    expect(
      within(de).getByRole('radio', { name: 'CSV (für Tabellenkalkulationen)' }),
    ).toBeChecked();
    expect(within(de).getByRole('button', { name: 'Abbrechen' })).toHaveFocus();
    expect(within(de).getByRole('textbox', { name: 'Zeilenlimit' })).toBeVisible();
  });
});

describe('export helpers', () => {
  it('formats sizes and checks the row limit', () => {
    expect(formatBytes(512, 'en')).toBe('512 B');
    expect(formatBytes(1536, 'en')).toBe('1.5 KB');
    expect(formatBytes(5 * 1024 * 1024, 'de')).toBe('5 MB');
    expect(parseRowLimit(' 42 ')).toBe(42);
    expect(parseRowLimit('1000000')).toBe(1_000_000);
    for (const bad of ['0', '1000001', '1e3', '1.0', '', ' ', '+5', '0x10']) {
      expect(parseRowLimit(bad)).toBeNull();
    }
  });
});
