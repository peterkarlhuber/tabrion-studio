/**
 * QA-S1 S1-32 in the views whose button opens a connection (owner decision of 2026-11-14 14:13Z): after the user's own
 * "Disconnect", "Show status" (migrations), "Run diagnosis" (diagnostics) and "Inspect" (vector data) open nothing.
 * They ask through the page's connection session (`askReconnect`, the banner with "Reconnect") and go on only when
 * "Reconnect" there opened the connection again. A profile that is not disconnected opens as before.
 */
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';

import { PROFILES } from '../harness/profiles';
import {
  ConnectionSessionContext,
  NO_CONNECTION_SESSION,
  type ConnectionSession,
} from '../workspace';
import { DiagnosticsView } from './diagnostics/DiagnosticsView';
import { MigrationsView } from './migrations/MigrationsView';
import { VectorsView } from './vectors/VectorsView';

type Call = { method: string; url: string };

/** Profiles, and every other request answered 404 after it was recorded (the views say so; nothing else matters). */
function server() {
  const calls: Call[] = [];
  const fetcher = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ method: init?.method ?? 'GET', url });
    if (url === '/api/profiles') return new Response(JSON.stringify(PROFILES));
    return new Response(
      JSON.stringify({ code: 'studio.not_found', kind: 'not_found', status: 404 }),
      { status: 404 },
    );
  }) as typeof fetch;
  return { fetcher, calls };
}

/** A session in which `disconnected` holds the profiles the user disconnected; the waiting resume is kept. */
function session(disconnected: Set<string>) {
  const waiting = new Map<string, (connectionId: string) => void>();
  const dropped: string[] = [];
  const value: ConnectionSession = {
    ...NO_CONNECTION_SESSION,
    prodConfirmed: () => true,
    isDisconnected: (profileId) => disconnected.has(profileId),
    askReconnect: vi.fn((profileId: string, resume: (connectionId: string) => void) => {
      waiting.set(profileId, resume);
      return () => {
        if (waiting.get(profileId) === resume) {
          waiting.delete(profileId);
          dropped.push(profileId);
        }
      };
    }),
  };
  /** "Reconnect" on the banner opened the profile again. */
  const reconnect = (profileId: string) => {
    disconnected.delete(profileId);
    const resume = waiting.get(profileId);
    waiting.delete(profileId);
    act(() => resume?.('c-new'));
  };
  return { value, reconnect, dropped };
}

const opens = (calls: Call[]) =>
  calls.filter((call) => call.method === 'POST' && call.url === '/api/connections');

const VIEWS: [string, (fetcher: typeof fetch) => ReactElement, string][] = [
  ['migrations "Show status"', (fetcher) => <MigrationsView fetcher={fetcher} />, 'Show status'],
  [
    'diagnostics "Run diagnosis"',
    (fetcher) => <DiagnosticsView fetcher={fetcher} />,
    'Run diagnosis',
  ],
  ['vector data "Inspect"', (fetcher) => <VectorsView fetcher={fetcher} />, 'Inspect'],
];

describe.each(VIEWS)('S1-32: %s', (_name, view, button) => {
  it('opens nothing after the user disconnected the profile; goes on after "Reconnect"', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server();
    const { value, reconnect } = session(new Set(['project:parts-dev']));
    render(
      <ConnectionSessionContext.Provider value={value}>
        {view(fetcher)}
      </ConnectionSessionContext.Provider>,
    );
    const press = await screen.findByRole('button', { name: button });
    await waitFor(() => expect(calls.some((call) => call.url === '/api/profiles')).toBe(true));
    await user.click(press);
    expect(value.askReconnect).toHaveBeenCalledWith('project:parts-dev', expect.any(Function));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(opens(calls)).toEqual([]);
    expect(screen.queryByRole('dialog')).toBeNull();

    reconnect('project:parts-dev');
    await waitFor(() => expect(opens(calls)).toHaveLength(1));
  });

  it('a profile that is not disconnected opens as before, without asking', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server();
    const { value } = session(new Set());
    render(
      <ConnectionSessionContext.Provider value={value}>
        {view(fetcher)}
      </ConnectionSessionContext.Provider>,
    );
    const press = await screen.findByRole('button', { name: button });
    await waitFor(() => expect(calls.some((call) => call.url === '/api/profiles')).toBe(true));
    await user.click(press);
    await waitFor(() => expect(opens(calls)).toHaveLength(1));
    expect(value.askReconnect).not.toHaveBeenCalled();
  });

  it('leaving the view drops the waiting press', async () => {
    const user = userEvent.setup();
    const { fetcher, calls } = server();
    const { value, reconnect, dropped } = session(new Set(['project:parts-dev']));
    const { unmount } = render(
      <ConnectionSessionContext.Provider value={value}>
        {view(fetcher)}
      </ConnectionSessionContext.Provider>,
    );
    const press = await screen.findByRole('button', { name: button });
    await waitFor(() => expect(calls.some((call) => call.url === '/api/profiles')).toBe(true));
    await user.click(press);
    unmount();
    expect(dropped).toEqual(['project:parts-dev']);
    reconnect('project:parts-dev');
    expect(opens(calls)).toEqual([]);
  });
});
