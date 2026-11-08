import { useEffect, useState } from 'react';

import { fetchHealth, type Health } from './heartbeat';

type State = { kind: 'loading' } | { kind: 'ok'; health: Health } | { kind: 'offline' };

/** Placeholder page of the skeleton (STU-001). The app shell, design system and i18n arrive with STU-005. */
export function App({ fetcher = fetch }: { fetcher?: typeof fetch }) {
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
    </main>
  );
}
