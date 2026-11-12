import { describe, expect, it } from 'vitest';

import { createQueryServer } from '../../harness/queries';
import { MAX_PAGES, RowCache } from './rowCache';

async function settle(): Promise<void> {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

describe('RowCache', () => {
  it('holds at most MAX_PAGES pages while a 100 000-row result is read from start to end', async () => {
    const server = createQueryServer();
    const fetcher: typeof fetch = async (input, init) =>
      (await server.handle(String(input), init)) ?? new Response(null, { status: 404 });
    const started = await fetcher('/api/queries', {
      method: 'POST',
      body: JSON.stringify({ sql: 'select * from generate_series(1, 100000)', max_rows: 100_000 }),
    });
    const { query_id: id } = (await started.json()) as { query_id: string };
    await new Promise((resolve) => setTimeout(resolve, 50));
    const cache = new RowCache(fetcher, id, 100_000);
    let most = 0;
    for (let index = 0; index < 100_000; index += 500) {
      expect(cache.get(index)).toBeUndefined();
      await settle();
      expect(cache.get(index)?.values[0]).toBe(index + 1);
      most = Math.max(most, cache.heldRows);
    }
    expect(most).toBe(MAX_PAGES * 500);
    expect(cache.heldPages).toBe(MAX_PAGES);
    expect(server.log.rows).toHaveLength(200);
    // A page still held is not fetched again; the cut cells are kept per row.
    cache.get(99_999);
    expect(server.log.rows).toHaveLength(200);
    cache.dispose();
    expect(cache.heldRows).toBe(0);
  });

  it('reports a result the server no longer keeps', async () => {
    const fetcher: typeof fetch = async () =>
      new Response(JSON.stringify({ code: 'studio.query_not_found' }), { status: 404 });
    const cache = new RowCache(fetcher, 'gone', 10);
    cache.get(0);
    await settle();
    expect(cache.problem?.code).toBe('studio.query_not_found');
  });
});
