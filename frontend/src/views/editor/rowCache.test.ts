import { describe, expect, it } from 'vitest';

import { createQueryServer } from '../../harness/queries';
import { CHUNK_ROWS, MAX_ATTEMPTS, MAX_ROWS, RowCache, rowBytes, valueBytes } from './rowCache';

async function settle(): Promise<void> {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

async function harnessQuery(sql: string, maxRows: number) {
  const server = createQueryServer();
  const fetcher: typeof fetch = async (input, init) =>
    (await server.handle(String(input), init)) ?? new Response(null, { status: 404 });
  const started = await fetcher('/api/queries', {
    method: 'POST',
    body: JSON.stringify({ sql, max_rows: maxRows }),
  });
  const { query_id: id } = (await started.json()) as { query_id: string };
  await new Promise((resolve) => setTimeout(resolve, 50));
  return { server, fetcher, id };
}

function rowsResponse(offset: number, count: number, width = 1): Response {
  const rows = Array.from({ length: count }, (_, i) => [offset + i + 1, 'x'.repeat(width)]);
  return new Response(JSON.stringify({ offset, rows, truncated_cells: [] }), { status: 200 });
}

describe('RowCache', () => {
  it('holds at most MAX_ROWS rows while a 100 000-row result is read from start to end', async () => {
    const { server, fetcher, id } = await harnessQuery(
      'select * from generate_series(1, 100000)',
      100_000,
    );
    const cache = new RowCache(fetcher, id, 100_000);
    let most = 0;
    for (let index = 0; index < 100_000; index += 500) {
      expect(cache.get(index)).toBeUndefined();
      await settle();
      expect(cache.get(index)?.values[0]).toBe(index + 1);
      // Every row of the fetch is there (no off-by-one at a chunk's edge).
      expect(cache.get(index + CHUNK_ROWS - 1)?.values[0]).toBe(index + CHUNK_ROWS);
      most = Math.max(most, cache.heldRows);
    }
    expect(most).toBe(MAX_ROWS);
    expect(cache.heldChunks).toBe(MAX_ROWS / CHUNK_ROWS);
    // The first fetch is one chunk; once a row's size is known a fetch is 500 narrow rows.
    expect(server.log.rows[0]).toEqual({ offset: 0, limit: CHUNK_ROWS });
    expect(server.log.rows[1]).toEqual({ offset: 500, limit: 500 });
    const fetches = server.log.rows.length;
    // A chunk still held is not fetched again.
    cache.get(99_999);
    expect(server.log.rows).toHaveLength(fetches);
    cache.dispose();
    expect(cache.heldRows).toBe(0);
  });

  it('bounds the bytes held, not only the rows: wide rows are fetched and kept in fewer chunks (M-4)', async () => {
    const { server, fetcher, id } = await harnessQuery(
      'select * from generate_series(1, 1000) wide(3000)',
      1_000,
    );
    const maxBytes = 2 * 1024 * 1024;
    const cache = new RowCache(fetcher, id, 1_000, { maxBytes, fetchBytes: 512 * 1024 });
    let most = 0;
    for (let index = 0; index < 1_000; index += CHUNK_ROWS) {
      cache.get(index);
      await settle();
      expect(cache.get(index)?.values[0]).toBe(index + 1);
      most = Math.max(most, cache.heldBytes);
    }
    // A row holds three texts of about 3 000 characters "€": about 18 KB.
    expect(rowBytes(cache.get(999)?.values ?? [])).toBeGreaterThan(18_000);
    // Never more than the budget plus the last fetch, far fewer rows than MAX_ROWS.
    expect(most).toBeLessThanOrEqual(maxBytes + 512 * 1024 + 20_000 * CHUNK_ROWS);
    expect(cache.heldRows).toBeLessThan(200);
    // Once the size is known, a fetch asks for fewer rows than a narrow one.
    expect(Math.max(...server.log.rows.map((page) => page.limit))).toBeLessThanOrEqual(CHUNK_ROWS);
    cache.dispose();
  });

  it('estimates text as UTF-16', () => {
    expect(valueBytes('€'.repeat(1000))).toBeGreaterThanOrEqual(2000);
    expect(valueBytes({ a: 'x'.repeat(100) })).toBeGreaterThan(200);
    expect(valueBytes(null)).toBeGreaterThan(0);
    expect(rowBytes([1, 'ab'])).toBeGreaterThan(valueBytes(1) + valueBytes('ab'));
  });

  it('reports a result the server no longer keeps, without asking again', async () => {
    let calls = 0;
    const fetcher: typeof fetch = async () => {
      calls += 1;
      return new Response(JSON.stringify({ code: 'studio.query_not_found' }), { status: 404 });
    };
    const cache = new RowCache(fetcher, 'gone', 10, { retryMs: 0 });
    cache.get(0);
    await settle();
    expect(cache.problem?.code).toBe('studio.query_not_found');
    cache.get(0);
    await settle();
    expect(calls).toBe(1);
  });

  it('asks again after a failed page and after a page that is not the one asked for (L-6)', async () => {
    const answers = [
      () => Promise.reject(new TypeError('offline')),
      () => Promise.resolve(rowsResponse(7, CHUNK_ROWS)),
      () => Promise.resolve(rowsResponse(0, 3)),
      () => Promise.resolve(rowsResponse(0, CHUNK_ROWS)),
    ];
    let calls = 0;
    const fetcher: typeof fetch = () => {
      const answer = answers[Math.min(calls, answers.length - 1)] as () => Promise<Response>;
      calls += 1;
      return answer();
    };
    const cache = new RowCache(fetcher, 'q', 100, { retryMs: 0 });
    let told = 0;
    cache.subscribe(() => {
      told += 1;
    });
    // A failure, then a wrong offset: still no problem, the next ask fetches again.
    for (let i = 0; i < MAX_ATTEMPTS - 1; i += 1) {
      cache.get(0);
      await settle();
      expect(cache.problem).toBeNull();
    }
    expect(cache.get(0)).toBeUndefined();
    await settle();
    // The third answer has the wrong row count: the third failure in a row shows the problem.
    expect(cache.problem?.code).toBe('studio.rows_invalid');
    expect(calls).toBe(MAX_ATTEMPTS);
    expect(told).toBeGreaterThanOrEqual(MAX_ATTEMPTS);
  });

  it('a success resets the failures, and the listeners are told when a retry is due', async () => {
    let calls = 0;
    const fetcher: typeof fetch = () => {
      calls += 1;
      return calls === 1
        ? Promise.resolve(new Response('{}', { status: 503 }))
        : Promise.resolve(rowsResponse(0, CHUNK_ROWS));
    };
    const cache = new RowCache(fetcher, 'q', 100, { retryMs: 100 });
    let told = 0;
    cache.subscribe(() => {
      told += 1;
    });
    cache.get(0);
    await settle();
    expect(told).toBe(1);
    // Not before the pause is over.
    cache.get(0);
    expect(calls).toBe(1);
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(told).toBe(2);
    cache.get(0);
    await settle();
    expect(cache.get(0)?.values[0]).toBe(1);
    expect(cache.problem).toBeNull();
  });
});
