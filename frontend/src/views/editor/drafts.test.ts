import { afterEach, describe, expect, it } from 'vitest';

import {
  DRAFTS_KEY,
  draftsKey,
  MAX_DRAFT_CHARS,
  MAX_TABS,
  parseDrafts,
  readDrafts,
  writeDrafts,
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

describe('drafts', () => {
  it('keep title, profile and text of every tab, and nothing else', () => {
    writeDrafts({
      tabs: [{ id: 'a', title: 'Query 1', profileId: 'user:x', sql: 'select :p', savedSql: '' }],
      activeTab: 'a',
    });
    expect(JSON.parse(localStorage.getItem(DRAFTS_KEY) ?? '')).toEqual({
      tabs: [{ id: 'a', title: 'Query 1', profile_id: 'user:x', sql: 'select :p', saved_sql: '' }],
      activeTab: 'a',
    });
    expect(readDrafts()?.tabs[0]?.sql).toBe('select :p');
  });

  it('ignore what they cannot read', () => {
    expect(parseDrafts('{')).toBeNull();
    expect(parseDrafts('{"tabs":[{"id":1}]}')).toBeNull();
    expect(
      parseDrafts('{"tabs":[{"id":"a","title":"T","sql":"x"}],"activeTab":"zz"}')?.activeTab,
    ).toBe('a');
  });

  it('do not store a text over the size cap, and do not read one', () => {
    const big = 'x'.repeat(MAX_DRAFT_CHARS + 1);
    const fits = 'y'.repeat(MAX_DRAFT_CHARS);
    writeDrafts({ tabs: [tab('a', big), tab('b', fits)], activeTab: 'a' });
    const stored = readDrafts();
    expect(stored?.tabs.map((item) => item.sql.length)).toEqual([0, MAX_DRAFT_CHARS]);
    expect(
      parseDrafts(JSON.stringify({ tabs: [{ id: 'a', title: 'T', sql: big }], activeTab: 'a' })),
    ).toBeNull();
    const many = Array.from({ length: MAX_TABS + 3 }, (_, i) => tab(`t${i}`));
    writeDrafts({ tabs: many, activeTab: 't0' });
    expect(readDrafts()?.tabs).toHaveLength(MAX_TABS);
  });

  it('are kept per project (L-5)', () => {
    const one = draftsKey('/home/a/project');
    const two = draftsKey('/home/b/project');
    expect(one).not.toBe(two);
    expect(one).not.toContain('/home');
    expect(draftsKey('')).toBe(DRAFTS_KEY);
    writeDrafts({ tabs: [tab('a', 'select 1')], activeTab: 'a' }, one);
    expect(readDrafts(one)?.tabs[0]?.sql).toBe('select 1');
    expect(readDrafts(two)).toBeNull();
    expect(readDrafts()).toBeNull();
  });

  it('keep the tabs another page added and drop the ones this page closed', () => {
    const key = draftsKey('/p');
    writeDrafts({ tabs: [tab('a'), tab('b')], activeTab: 'a' }, key);
    // Another page added c.
    writeDrafts({ tabs: [tab('a'), tab('b'), tab('c', 'other')], activeTab: 'c' }, key);
    // This page knew a and b, closed b and edited a.
    writeDrafts({ tabs: [tab('a', 'mine')], activeTab: 'a' }, key, new Set(['a', 'b']));
    expect(readDrafts(key)?.tabs.map((item) => [item.id, item.sql])).toEqual([
      ['a', 'mine'],
      ['c', 'other'],
    ]);
  });
});

describe('drafts of project scripts (STU-020)', () => {
  it('keep the script path and digest of a tab, and drop a malformed digest', () => {
    const digest = 'c'.repeat(64);
    writeDrafts(
      {
        tabs: [
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
        ],
        activeTab: 'a',
      },
      'k',
    );
    expect(readDrafts('k')?.tabs).toEqual([
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
    ]);
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
