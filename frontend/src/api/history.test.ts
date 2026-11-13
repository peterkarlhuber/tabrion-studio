import { deleteHistory, fetchHistory, parseFavorites, parseHistory, setFavorite } from './history';
import {
  parseScriptFile,
  parseScriptList,
  readScript,
  scriptTitle,
  validScriptPath,
} from './scripts';

const ENTRY = {
  id: 7,
  profile_id: 'project:parts',
  environment: 'prod',
  engine: 'postgresql',
  sql: 'select * from parts where id = :id',
  sql_truncated: false,
  param_names: ['id'],
  state: 'done',
  row_count: 1,
  elapsed_ms: 2.5,
  code: null,
  finished_at: 1_700_000_000.5,
};

describe('history answers (STU-020)', () => {
  it('reads entries and drops an entry of another shape (fail closed)', () => {
    const page = parseHistory({
      enabled: true,
      retention_days: 30,
      entries: [
        ENTRY,
        { ...ENTRY, id: 'x' },
        { ...ENTRY, state: 'running' },
        { ...ENTRY, environment: 'qa' },
      ],
      next_before: null,
    });
    expect(page?.entries).toEqual([
      {
        id: 7,
        profileId: 'project:parts',
        environment: 'prod',
        engine: 'postgresql',
        sql: 'select * from parts where id = :id',
        sqlTruncated: false,
        paramNames: ['id'],
        state: 'done',
        rowCount: 1,
        elapsedMs: 2.5,
        code: null,
        finishedAt: 1_700_000_000_500,
      },
    ]);
    expect(parseHistory({ entries: [] })).toBeNull();
    expect(parseHistory(null)).toBeNull();
  });

  it('asks with the filters and deletes with a JSON body', async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const fetcher: typeof fetch = async (input, init) => {
      calls.push({ url: String(input), init });
      return new Response(
        JSON.stringify(
          init?.method === 'DELETE'
            ? { deleted: 3 }
            : { enabled: true, retention_days: 30, entries: [], next_before: null },
        ),
      );
    };
    await fetchHistory(fetcher, { profileId: 'user:a', q: 'from x', limit: 50, before: 9 });
    expect(calls[0]?.url).toBe('/api/history?profile_id=user%3Aa&q=from+x&limit=50&before=9');
    expect(await deleteHistory(fetcher, { profileId: 'user:a' })).toEqual({ ok: true, value: 3 });
    expect(calls[1]?.init?.method).toBe('DELETE');
    expect(JSON.parse(String(calls[1]?.init?.body))).toEqual({ profile_id: 'user:a' });
  });

  it('reads favorites and recent profiles', async () => {
    expect(
      parseFavorites({
        favorites: [{ profile_id: 'user:a', added_at: 1 }, { profile_id: 3 }],
        recent: [{ profile_id: 'user:b', opened_at: 2 }, { profile_id: 'user:c' }],
      }),
    ).toEqual({ favorites: ['user:a'], recent: [{ profileId: 'user:b', openedAt: 2000 }] });
    expect(parseFavorites({ favorites: [] })).toBeNull();
    const bad: typeof fetch = async () => new Response(JSON.stringify({ nope: true }));
    expect(await setFavorite(bad, 'user:a', true)).toMatchObject({
      ok: false,
      problem: { code: 'studio.answer_invalid' },
    });
  });
});

describe('script answers (STU-020)', () => {
  it('reads the list and a file', () => {
    expect(
      parseScriptList({ scripts: [{ path: 'a.sql', size: 3 }, { path: 1 }], truncated: true }),
    ).toEqual({ scripts: [{ path: 'a.sql', size: 3 }], truncated: true, skippedLinks: 0 });
    expect(parseScriptList({ scripts: [], truncated: false, skipped_links: 2 })?.skippedLinks).toBe(
      2,
    );
    expect(parseScriptList({ scripts: [], skipped_links: 'x' })?.skippedLinks).toBe(0);
    expect(parseScriptFile({ path: 'a.sql', content: 'x', digest: 'd' })).toEqual({
      path: 'a.sql',
      content: 'x',
      digest: 'd',
    });
    expect(parseScriptFile({ path: 'a.sql', content: 1, digest: 'd' })).toBeNull();
  });

  it('refuses the answer for another script than the one asked for', async () => {
    const fetcher: typeof fetch = async () =>
      new Response(JSON.stringify({ path: 'other.sql', content: 'x', digest: 'd' }));
    expect(await readScript(fetcher, 'a.sql')).toMatchObject({ ok: false });
  });

  it('checks a path like the server does, and names a tab after the script', () => {
    for (const ok of [
      'a.sql',
      'sql/reports/b.SQL',
      'x y.sql',
      'console.sql',
      'com10.sql',
      'a~b.sql',
    ])
      expect(validScriptPath(ok)).toBe(true);
    for (const bad of [
      '',
      '.sql',
      '/abs.sql',
      '../x.sql',
      'sql/../x.sql',
      'sql//x.sql',
      '.git/x.sql',
      'a\\b.sql',
      'C:x.sql',
      'notes.txt',
      ' a.sql',
      'a\n.sql',
      `${'x'.repeat(520)}.sql`,
      // Windows device names, 8.3 short names, trailing dots and format characters (the server's rules).
      'nul.sql',
      'CON.sql',
      'com1.sql',
      'lpt\u00b9.sql',
      'conin$.sql',
      'aux/x.sql',
      'GIT~1/x.sql',
      'sql./x.sql',
      'a.sql.',
      'a\u202Eb.sql',
      'a\u200Bb.sql',
    ]) {
      expect(validScriptPath(bad)).toBe(false);
    }
    expect(scriptTitle('sql/reports/b.SQL')).toBe('b');
  });
});
