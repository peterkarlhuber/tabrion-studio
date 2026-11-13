import { afterEach, describe, expect, it } from 'vitest';

import { createDraftsServer } from '../../harness/drafts';
import {
  clearLegacyDrafts,
  DRAFTS_KEY,
  draftsDocument,
  draftsKey,
  fetchDrafts,
  hasLegacyDrafts,
  legacyKept,
  MAX_DRAFT_CHARS,
  MAX_TABS,
  MAX_TOTAL_CHARS,
  mergeDrafts,
  parseDrafts,
  parseDraftsAnswer,
  readLegacyDrafts,
  saveDrafts,
  wellFormed,
  type Drafts,
  type DraftTab,
} from './drafts';

const tab = (id: string, sql = ''): DraftTab => ({
  id,
  title: `T ${id}`,
  profileId: '',
  sql,
  savedSql: '',
});

afterEach(() => localStorage.clear());

function fake(server: ReturnType<typeof createDraftsServer>): typeof fetch {
  return async (input, init) =>
    server.handle(String(input), init) ?? new Response(null, { status: 404 });
}

describe('drafts on the server (TD-033, review F-5)', () => {
  it('keep title, profile and text of every tab, and nothing else', () => {
    expect(
      draftsDocument({
        tabs: [{ id: 'a', title: 'Query 1', profileId: 'user:x', sql: 'select :p', savedSql: '' }],
        activeTab: 'a',
      }),
    ).toEqual({
      tabs: [{ id: 'a', title: 'Query 1', profile_id: 'user:x', sql: 'select :p', saved_sql: '' }],
      active_tab: 'a',
    });
  });

  it('are saved and read through the API, never through browser storage', async () => {
    const server = createDraftsServer();
    const fetcher = fake(server);
    expect(await fetchDrafts(fetcher)).toEqual({ ok: true, value: null });
    expect(
      await saveDrafts(fetcher, { tabs: [tab('a', 'select 1')], activeTab: 'a' }, new Set(['a'])),
    ).toBe(true);
    expect(server.log.puts).toEqual([
      {
        tabs: [{ id: 'a', title: 'T a', profile_id: '', sql: 'select 1', saved_sql: '' }],
        active_tab: 'a',
        known: ['a'],
      },
    ]);
    expect(await fetchDrafts(fetcher)).toEqual({
      ok: true,
      value: { tabs: [tab('a', 'select 1')], activeTab: 'a' },
    });
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it('replace a lone surrogate, which the server refuses, and keep every other character', () => {
    const text = `a\uD800b\uDC00c\uD83D\uDE00d`;
    const doc = draftsDocument({ tabs: [{ ...tab('a', text), title: '\uDBFF' }], activeTab: 'a' });
    expect(doc.tabs[0]?.sql).toBe('a\uFFFDb\uFFFDc\uD83D\uDE00d');
    expect(doc.tabs[0]?.sql.length).toBe(text.length);
    expect(doc.tabs[0]?.title).toBe('\uFFFD');
    expect(wellFormed('plain')).toBe('plain');
  });

  it('report a server that cannot keep them', async () => {
    const fetcher = fake(createDraftsServer({ unavailable: true }));
    const read = await fetchDrafts(fetcher);
    expect(read.ok).toBe(false);
    expect(await saveDrafts(fetcher, { tabs: [tab('a')], activeTab: 'a' })).toBe(false);
  });

  it('read the answer strictly', () => {
    expect(parseDraftsAnswer({})).toBeNull();
    expect(parseDraftsAnswer({ drafts: 'x' })).toBeNull();
    expect(parseDraftsAnswer({ drafts: null })).toEqual({ drafts: null });
    expect(
      parseDraftsAnswer({
        drafts: { tabs: [{ id: 'a', title: 'T', sql: 'x' }], active_tab: 'zz' },
      }),
    ).toEqual({
      drafts: {
        tabs: [{ id: 'a', title: 'T', profileId: '', sql: 'x', savedSql: '' }],
        activeTab: 'a',
      },
    });
  });

  it('leave out a text over the cap of one text, and the largest texts while all are over theirs', () => {
    const big = 'x'.repeat(MAX_DRAFT_CHARS + 1);
    const fits = 'y'.repeat(MAX_DRAFT_CHARS);
    expect(
      draftsDocument({ tabs: [tab('a', big), tab('b', fits)], activeTab: 'a' }).tabs.map(
        (item) => item.sql.length,
      ),
    ).toEqual([0, MAX_DRAFT_CHARS]);
    const count = MAX_TOTAL_CHARS / MAX_DRAFT_CHARS;
    const full = Array.from({ length: count }, (_, i) => tab(`t${i}`, fits));
    const doc = draftsDocument({ tabs: [...full, tab('small', 'select 1')], activeTab: 't0' });
    const lengths = doc.tabs.map((item) => item.sql.length);
    expect(lengths.reduce((sum, length) => sum + length, 0)).toBeLessThanOrEqual(MAX_TOTAL_CHARS);
    expect(lengths.at(-1)).toBe('select 1'.length);
    expect(lengths.filter((length) => length === 0)).toHaveLength(1);
    const many = Array.from({ length: MAX_TABS + 3 }, (_, i) => tab(`t${i}`));
    expect(draftsDocument({ tabs: many, activeTab: 't0' }).tabs).toHaveLength(MAX_TABS);
    expect(
      parseDrafts(JSON.stringify({ tabs: [{ id: 'a', title: 'T', sql: big }], activeTab: 'a' })),
    ).toBeNull();
  });

  it('keep the tabs another page added and drop the ones this page closed (server merge)', async () => {
    const server = createDraftsServer();
    const fetcher = fake(server);
    await saveDrafts(fetcher, { tabs: [tab('a'), tab('b')], activeTab: 'a' }, new Set(['a', 'b']));
    // Another page added c.
    await saveDrafts(
      fetcher,
      { tabs: [tab('a'), tab('b'), tab('c', 'other')], activeTab: 'c' },
      new Set(['a', 'b', 'c']),
    );
    // This page knew a and b, closed b and edited a.
    await saveDrafts(fetcher, { tabs: [tab('a', 'mine')], activeTab: 'a' }, new Set(['a', 'b']));
    expect(server.current()?.tabs.map((item) => [item.id, item.sql])).toEqual([
      ['a', 'mine'],
      ['c', 'other'],
    ]);
  });
});

describe('drafts of an earlier version in browser storage (migration)', () => {
  const legacy = (tabs: DraftTab[]) =>
    JSON.stringify({
      tabs: tabs.map((item) => ({ id: item.id, title: item.title, sql: item.sql })),
      activeTab: tabs[0]?.id,
    });

  it('are read for this project and from the unscoped key; only those two keys are removed', () => {
    expect(hasLegacyDrafts('/p')).toBe(false);
    localStorage.setItem(draftsKey('/other'), legacy([tab('o', 'other')]));
    // Another project's key is not this project's draft.
    expect(hasLegacyDrafts('/p')).toBe(false);
    localStorage.setItem(draftsKey('/p'), legacy([tab('a', 'one')]));
    localStorage.setItem(DRAFTS_KEY, legacy([tab('a', 'dup'), tab('u', 'unscoped')]));
    localStorage.setItem('tabrion-studio.theme', 'dark');
    expect(hasLegacyDrafts('/p')).toBe(true);
    expect(readLegacyDrafts('/p')?.tabs.map((item) => item.sql)).toEqual(['one', 'unscoped']);
    expect(readLegacyDrafts('')?.tabs.map((item) => item.sql)).toEqual(['dup', 'unscoped']);
    clearLegacyDrafts('/p');
    expect(hasLegacyDrafts('/p')).toBe(false);
    // Other projects' keys stay until their own editor moves them (review finding 3).
    expect(Object.keys(localStorage).sort()).toEqual([draftsKey('/other'), 'tabrion-studio.theme']);
    expect(hasLegacyDrafts('/other')).toBe(true);
    clearLegacyDrafts('/other');
    expect(Object.keys(localStorage)).toEqual(['tabrion-studio.theme']);
  });

  it('are removed only when the saved document keeps all of them', () => {
    const legacyDrafts = { tabs: [tab('l1', 'old'), tab('l2', 'older')], activeTab: 'l1' };
    const kept = (server: Drafts, legacy: Drafts) => {
      const merged = mergeDrafts(server, legacy);
      return merged !== null && legacyKept(merged, legacy);
    };
    expect(kept({ tabs: [tab('s')], activeTab: 's' }, legacyDrafts)).toBe(true);
    // Beyond the tab cap: some legacy tabs would be lost.
    const full = {
      tabs: Array.from({ length: MAX_TABS }, (_, i) => tab(`s${i}`)),
      activeTab: 's0',
    };
    expect(kept(full, legacyDrafts)).toBe(false);
    // Beyond the total text cap: some text would be left out.
    const big = 'x'.repeat(MAX_DRAFT_CHARS);
    const heavy = { tabs: Array.from({ length: 4 }, (_, i) => tab(`h${i}`, big)), activeTab: 'h0' };
    expect(kept(heavy, legacyDrafts)).toBe(false);
  });

  it('keep their key per project (L-5) and cannot be read when malformed', () => {
    const one = draftsKey('/home/a/project');
    expect(one).not.toBe(draftsKey('/home/b/project'));
    expect(one).not.toContain('/home');
    expect(draftsKey('')).toBe(DRAFTS_KEY);
    expect(parseDrafts('{')).toBeNull();
    expect(parseDrafts('{"tabs":[{"id":1}]}')).toBeNull();
    expect(
      parseDrafts('{"tabs":[{"id":"a","title":"T","sql":"x"}],"activeTab":"zz"}')?.activeTab,
    ).toBe('a');
  });

  it('merge after the server drafts, by tab id', () => {
    expect(mergeDrafts(null, null)).toBeNull();
    const server = { tabs: [tab('a', 'server')], activeTab: 'a' };
    const local = { tabs: [tab('a', 'local'), tab('b', 'local b')], activeTab: 'b' };
    expect(mergeDrafts(server, local)).toEqual({
      tabs: [tab('a', 'server'), tab('b', 'local b')],
      activeTab: 'a',
    });
    expect(mergeDrafts(null, local)).toBe(local);
  });
});

describe('drafts of project scripts (STU-020)', () => {
  it('keep the script path and digest of a tab, and drop a malformed digest', async () => {
    const digest = 'c'.repeat(64);
    const server = createDraftsServer();
    const fetcher = fake(server);
    const tabs: DraftTab[] = [
      {
        id: 'a',
        title: 'report',
        profileId: '',
        sql: 'x',
        savedSql: 'x',
        scriptPath: 'sql/report.sql',
        scriptDigest: digest,
      },
      { id: 'b', title: 'plain', profileId: '', sql: 'y', savedSql: '' },
    ];
    await saveDrafts(fetcher, { tabs, activeTab: 'a' });
    expect((await fetchDrafts(fetcher)).ok && (await fetchDrafts(fetcher))).toEqual({
      ok: true,
      value: { tabs, activeTab: 'a' },
    });
    const raw = JSON.stringify({
      tabs: [{ id: 'a', title: 't', sql: '', script_path: 'a.sql', script_digest: 'not-a-digest' }],
      activeTab: 'a',
    });
    expect(parseDrafts(raw)?.tabs[0]).toEqual({
      id: 'a',
      title: 't',
      profileId: '',
      sql: '',
      savedSql: '',
    });
  });
});
