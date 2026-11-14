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
      verification: { ok: false, records: 0, problem: null, at: null },
      records: [],
    });
  });
});
