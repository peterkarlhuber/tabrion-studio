/** TD-034: the fetcher wrapper that sees lost and opened connections in every answer, without changing one. */
import { requestConnection, watchConnections } from './connectionWatch';

const problem = (code: string, status: number) =>
  new Response(JSON.stringify({ code, kind: 'x', status }), { status });

function watched(answer: () => Response) {
  const hooks = { lost: vi.fn(), opened: vi.fn(), listed: vi.fn() };
  const fetcher = watchConnections(vi.fn(async () => answer()) as unknown as typeof fetch, hooks);
  return { fetcher, hooks };
}

describe('requestConnection', () => {
  it('reads the connection from the path or from the body of a query or an export', () => {
    expect(requestConnection('/api/connections/c_1/catalog?refresh=true', undefined)).toBe('c_1');
    expect(requestConnection('/api/connections/c%2F2/tables/main/t/rows', undefined)).toBe('c/2');
    const body = JSON.stringify({ connection_id: 'c_3', sql: 'select 1' });
    expect(requestConnection('/api/queries', { method: 'POST', body })).toBe('c_3');
    expect(requestConnection('/api/exports', { method: 'POST', body })).toBe('c_3');
  });

  it('names no connection for the list, the open, a query page or a body it cannot read', () => {
    expect(requestConnection('/api/connections', { method: 'POST', body: '{}' })).toBeNull();
    expect(requestConnection('/api/connections/c_1', undefined)).toBeNull();
    expect(requestConnection('/api/queries/q_1/rows', undefined)).toBeNull();
    expect(requestConnection('/api/queries', { method: 'POST', body: 'not json' })).toBeNull();
    expect(requestConnection('/api/queries', { method: 'GET' })).toBeNull();
    expect(requestConnection('/api/connections/%E0/catalog', undefined)).toBeNull();
  });
});

describe('watchConnections', () => {
  it.each(['db.connection', 'studio.connection_not_found', 'studio.connection_stale'])(
    'reports %s on a request of a connection, and passes the answer on unread',
    async (code) => {
      const { fetcher, hooks } = watched(() => problem(code, 502));
      const response = await fetcher('/api/connections/c_1/catalog');
      expect(hooks.lost).toHaveBeenCalledWith('c_1', code);
      expect(((await response.json()) as { code: string }).code).toBe(code); // the caller still reads it
    },
  );

  it('reports nothing for other refusals, for answers that are fine, and for a Disconnect', async () => {
    for (const answer of [
      () => problem('db.timeout', 504),
      () => problem('studio.object_not_found', 404),
      () => new Response('{}', { status: 200 }),
      () => new Response('not json', { status: 502 }),
    ]) {
      const { fetcher, hooks } = watched(answer);
      await fetcher('/api/connections/c_1/table?schema=main&name=t');
      expect(hooks.lost).not.toHaveBeenCalled();
    }
    const { fetcher, hooks } = watched(() => problem('studio.connection_not_found', 404));
    await fetcher('/api/connections', { method: 'DELETE', body: '{"connection_id":"c_1"}' });
    expect(hooks.lost).not.toHaveBeenCalled();
  });

  it('reports a query that could not start on a lost connection', async () => {
    const { fetcher, hooks } = watched(() => problem('db.connection', 502));
    await fetcher('/api/queries', { method: 'POST', body: '{"connection_id":"c_9","sql":"x"}' });
    expect(hooks.lost).toHaveBeenCalledWith('c_9', 'db.connection');
  });

  it('reports an opened connection and the listed ones', async () => {
    const opened = { connection_id: 'c_2', profile_id: 'user:parts', environment: 'prod' };
    const open = watched(() => new Response(JSON.stringify(opened), { status: 201 }));
    const response = await open.fetcher('/api/connections', { method: 'POST', body: '{}' });
    expect(open.hooks.opened).toHaveBeenCalledWith({
      connectionId: 'c_2',
      profileId: 'user:parts',
      environment: 'prod',
    });
    expect(((await response.json()) as { connection_id: string }).connection_id).toBe('c_2');
    const list = watched(() => new Response(JSON.stringify({ connections: [opened] })));
    await list.fetcher('/api/connections');
    expect(list.hooks.listed).toHaveBeenCalledWith([
      { connectionId: 'c_2', profileId: 'user:parts', environment: 'prod' },
    ]);
    const refused = watched(() => problem('db.connection', 502));
    await refused.fetcher('/api/connections', { method: 'POST', body: '{}' });
    expect(refused.hooks.opened).not.toHaveBeenCalled();
    expect(refused.hooks.lost).not.toHaveBeenCalled();
  });
});
