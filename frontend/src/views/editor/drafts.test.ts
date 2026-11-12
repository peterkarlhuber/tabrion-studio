import { afterEach, describe, expect, it } from 'vitest';

import { DRAFTS_KEY, parseDrafts, readDrafts, writeDrafts } from './drafts';

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
});
