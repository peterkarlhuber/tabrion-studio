/** STU-XW-2: the audit log's state in the write profile section: verified, failed, pending records (review N-4). */
import { render, screen } from '@testing-library/react';

import { parseAudit } from '../../api/write';
import { AuditStatus } from './AuditStatus';

function fetcherFor(body: unknown, status = 200): { fetcher: typeof fetch; urls: string[] } {
  const urls: string[] = [];
  const fetcher: typeof fetch = async (input) => {
    urls.push(String(input));
    return new Response(JSON.stringify(body), { status });
  };
  return { fetcher, urls };
}

const RECORD = {
  seq: 2,
  time: '2026-11-14T12:00:00.000Z',
  event: 'unlock',
  fields: { profile: 'parts-write', result: 'ok' },
};

describe('AuditStatus (STU-XW-2)', () => {
  it('shows a verified log and its newest records', async () => {
    const { fetcher, urls } = fetcherFor({
      available: true,
      problem: null,
      pending: 0,
      verification: { ok: true, records: 2, problem: null, at: null },
      records: [RECORD],
    });
    render(<AuditStatus fetcher={fetcher} />);
    expect(await screen.findByText(/Verified: 2 records/)).toBeTruthy();
    expect(screen.getByText('parts-write')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(urls).toEqual(['/api/write/audit?limit=20']);
  });

  it('warns when the log failed verification', async () => {
    const { fetcher } = fetcherFor({
      available: false,
      problem: 'audit.record_changed',
      pending: 0,
      verification: { ok: false, records: 2, problem: 'audit.record_changed', at: 3 },
      records: [],
    });
    render(<AuditStatus fetcher={fetcher} />);
    expect((await screen.findByRole('alert')).textContent).toMatch(/failed verification/);
  });

  it('warns about pending records (N-4)', async () => {
    const { fetcher } = fetcherFor({
      available: true,
      problem: null,
      pending: 1,
      verification: { ok: true, records: 2, problem: null, at: null },
      records: [],
    });
    render(<AuditStatus fetcher={fetcher} />);
    expect((await screen.findByRole('alert')).textContent).toMatch(/1 record could not be written/);
  });

  it('says so when the state cannot be read', async () => {
    const { fetcher } = fetcherFor({ code: 'studio.method_not_allowed' }, 405);
    render(<AuditStatus fetcher={fetcher} />);
    expect((await screen.findByRole('alert')).textContent).toMatch(/could not be read/);
  });

  it('parses defensively', () => {
    expect(parseAudit(null)).toBeNull();
    const state = parseAudit({
      available: 'yes',
      pending: -3,
      verification: { ok: 'true' },
      records: [1, null],
    });
    expect(state).toEqual({
      available: false,
      problem: null,
      pending: 0,
      anchor: 'none',
      resets: 0,
      verification: { ok: false, records: 0, problem: null, at: null },
      records: [],
    });
  });
});

describe('AuditStatus: the keyring anchor (G-S2 3A)', () => {
  const base = {
    available: true,
    problem: null,
    pending: 0,
    verification: { ok: true, records: 3, problem: null, at: null },
    records: [],
  };

  it('reports a log found missing or reset for as long as its reset records are in the chain', async () => {
    const { fetcher } = fetcherFor({ ...base, anchor: 'ok', resets: 2 });
    render(<AuditStatus fetcher={fetcher} />);
    expect((await screen.findByTestId('write-audit-reset')).textContent).toMatch(
      /found missing or reset 2 times/,
    );
  });

  it('reports a reset found at this start', async () => {
    const { fetcher } = fetcherFor({ ...base, anchor: 'reset', resets: 1 });
    render(<AuditStatus fetcher={fetcher} />);
    expect((await screen.findByTestId('write-audit-reset')).textContent).toMatch(/reset once/);
  });

  it('says when no keyring keeps the anchor, and when it failed', async () => {
    const first = fetcherFor({ ...base, anchor: 'unavailable', resets: 0 });
    const view = render(<AuditStatus fetcher={first.fetcher} />);
    expect((await screen.findByTestId('write-audit-anchor')).textContent).toMatch(
      /No usable keyring/,
    );
    expect(screen.queryByTestId('write-audit-reset')).toBeNull();
    view.unmount();
    const second = fetcherFor({ ...base, anchor: 'failed', resets: 0 });
    render(<AuditStatus fetcher={second.fetcher} />);
    expect((await screen.findByTestId('write-audit-anchor')).textContent).toMatch(
      /could not be updated/,
    );
  });

  it('parses the anchor defensively', () => {
    expect(parseAudit({ ...base, anchor: 'whatever', resets: -1 })).toMatchObject({
      anchor: 'none',
      resets: 0,
    });
  });
});
