import { useEffect, useState } from 'react';

import { fetchHealth, type Health } from './heartbeat';
import type { SessionState } from './session';

type State = { kind: 'loading' } | { kind: 'ok'; health: Health } | { kind: 'offline' };

const SESSION_TEXT: Record<Exclude<SessionState, 'ok'>, string> = {
  used: 'This sign-in link was already used. Run tabrion-studio again to open Studio.',
  expired: 'This sign-in link has expired. Run tabrion-studio again to open Studio.',
  invalid: 'This sign-in link is not valid. Run tabrion-studio again to open Studio.',
  none: 'Not signed in. Run tabrion-studio in your project folder to open Studio.',
  offline: 'The local server does not answer.',
};

/** Placeholder page of the skeleton (STU-001). The app shell, design system and i18n arrive with STU-005. */
export function App({
  fetcher = fetch,
  session = 'ok',
}: {
  fetcher?: typeof fetch;
  session?: SessionState;
}) {
  const [state, setState] = useState<State>({ kind: 'loading' });

  useEffect(() => {
    let active = true;
    void fetchHealth(fetcher).then((health) => {
      if (active) {
        setState(health === null ? { kind: 'offline' } : { kind: 'ok', health });
      }
    });
    return () => {
      active = false;
    };
  }, [fetcher]);

  return (
    <main className="placeholder">
      <h1>Tabrion Studio</h1>
      <p>
        Local developer tool for SQLite and PostgreSQL databases. This is the pre-alpha skeleton;
        the workspace arrives with the next work packages.
      </p>
      <p role="status" data-testid="backend-status">
        {state.kind === 'loading' && 'Connecting to the local server…'}
        {state.kind === 'ok' && `Local server ${state.health.version}: ${state.health.status}`}
        {state.kind === 'offline' && 'The local server does not answer.'}
      </p>
      {session !== 'ok' && session !== 'offline' && (
        <p role="alert" data-testid="session-status">
          {SESSION_TEXT[session]}
        </p>
      )}
    </main>
  );
}
