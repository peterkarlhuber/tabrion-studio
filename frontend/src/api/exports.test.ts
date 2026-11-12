/**
 * TD-043 (STU-021 in the page): the export client. The request body for a statement and for a table, the file name
 * rule (the server's name only in its form), the media type, a JSON file without its end, the cut marker, progress,
 * abort, refusals and a server that does not answer.
 */
import { downloadExport, exportFilename, type ExportRequest } from './exports';

const SQL: ExportRequest = {
  connectionId: 'c1',
  format: 'csv',
  source: { kind: 'sql', sql: 'select :a', params: { a: 1 } },
  maxRows: 500,
  escapeFormulas: true,
};

function file(body: string | ReadableStream<Uint8Array>, type: string, name = 'export.csv') {
  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': type, 'Content-Disposition': `attachment; filename="${name}"` },
  });
}

function recorder(answer: () => Response | Promise<Response>) {
  const calls: { url: string; init: RequestInit }[] = [];
  const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init: init ?? {} });
    return answer();
  }) as typeof fetch;
  return { fetcher, calls };
}

describe('exportFilename', () => {
  it("keeps the server's name only in its form", () => {
    expect(exportFilename('attachment; filename="export.csv"', 'csv')).toBe('export.csv');
    expect(exportFilename('attachment; filename="parts_2026.v1.json"', 'json')).toBe(
      'parts_2026.v1.json',
    );
    expect(exportFilename('attachment; filename="export.csv"', 'json')).toBe('export.json');
    for (const bad of [
      null,
      '',
      'inline; filename="export.csv"',
      'attachment; filename="../../x.csv"',
      'attachment; filename=".bashrc.csv"',
      'attachment; filename="a..b.csv"',
      'attachment; filename="a/b.csv"',
      'attachment; filename="a b.csv"',
      'attachment; filename="‮gnp.csv"',
      'attachment; filename="x.csv.exe"',
      'attachment; filename=".csv"',
      `attachment; filename="${'a'.repeat(101)}.csv"`,
      `attachment; filename="export.csv"; filename*=UTF-8''evil.csv`,
    ]) {
      expect(exportFilename(bad, 'csv')).toBe('export.csv');
    }
  });
});

describe('downloadExport', () => {
  it('posts a statement with its parameters, the row limit and the formula option as JSON', async () => {
    const { fetcher, calls } = recorder(() => file('a\r\n1\r\n', 'text/csv; charset=utf-8'));
    const progress: number[] = [];
    const result = await downloadExport(fetcher, SQL, {
      onProgress: (bytes) => progress.push(bytes),
    });
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe('/api/exports');
    expect(calls[0]?.init.method).toBe('POST');
    expect((calls[0]?.init.headers as Record<string, string>)['Content-Type']).toBe(
      'application/json',
    );
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({
      connection_id: 'c1',
      format: 'csv',
      sql: 'select :a',
      params: { a: 1 },
      max_rows: 500,
      escape_formulas: true,
    });
    expect(result.ok && result.value.filename).toBe('export.csv');
    expect(result.ok && result.value.bytes).toBe(6);
    expect(result.ok && result.value.truncated).toBeNull();
    expect(result.ok && (await result.value.blob.text())).toBe('a\r\n1\r\n');
    expect(result.ok && result.value.blob.type).toBe('text/csv');
    expect(progress.at(-1)).toBe(6);
  });

  it('posts a table without SQL, and JSON without the formula option', async () => {
    const { fetcher, calls } = recorder(() =>
      file(
        '{"columns":["a"],"rows":[\n[1]\n],"truncated":null}\n',
        'application/json',
        'parts.json',
      ),
    );
    const result = await downloadExport(fetcher, {
      ...SQL,
      format: 'json',
      source: { kind: 'table', schema: 'public', name: 'parts' },
    });
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({
      connection_id: 'c1',
      format: 'json',
      table: { schema: 'public', name: 'parts' },
      max_rows: 500,
    });
    expect(result.ok && result.value.filename).toBe('parts.json');
    expect(result.ok && result.value.truncated).toBe(false);
  });

  it('reads the cut marker of a JSON file, and refuses one without its end', async () => {
    const cut = await downloadExport(
      recorder(() =>
        file('{"columns":["a"],"rows":[\n[1]\n],"truncated":"rows"}\n', 'application/json'),
      ).fetcher,
      { ...SQL, format: 'json' },
    );
    expect(cut.ok && cut.value.truncated).toBe(true);
    const broken = await downloadExport(
      recorder(() => file('{"columns":["a"],"rows":[\n[1]', 'application/json')).fetcher,
      { ...SQL, format: 'json' },
    );
    expect(broken).toEqual({
      ok: false,
      problem: expect.objectContaining({ code: 'studio.export_incomplete' }),
    });
  });

  it('refuses another media type than the one asked for', async () => {
    const result = await downloadExport(
      recorder(() => file('<html></html>', 'text/html')).fetcher,
      SQL,
    );
    expect(result).toEqual({
      ok: false,
      problem: expect.objectContaining({ code: 'studio.export_incomplete' }),
    });
  });

  it("returns the server's problem, and offline when it does not answer", async () => {
    const refused = await downloadExport(
      recorder(
        () =>
          new Response(JSON.stringify({ code: 'studio.export_busy', kind: 'database' }), {
            status: 429,
          }),
      ).fetcher,
      SQL,
    );
    expect(refused.ok === false && refused.problem.code).toBe('studio.export_busy');
    const offline = await downloadExport(
      recorder(() => {
        throw new TypeError('network');
      }).fetcher,
      SQL,
    );
    expect(offline.ok === false && offline.problem.code).toBe('studio.offline');
  });

  it('an abort during the stream is a cancel, and nothing is returned', async () => {
    const abort = new AbortController();
    let push: ((chunk: string) => void) | null = null;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (chunk) => controller.enqueue(new TextEncoder().encode(chunk));
        abort.signal.addEventListener('abort', () =>
          controller.error(new DOMException('aborted', 'AbortError')),
        );
      },
    });
    const pending = downloadExport(recorder(() => file(stream, 'text/csv')).fetcher, SQL, {
      signal: abort.signal,
      onProgress: () => abort.abort(),
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    (push as unknown as (chunk: string) => void)('a\r\n');
    const result = await pending;
    expect(result.ok === false && result.problem.code).toBe('studio.export_canceled');
  });
});
