import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { startAppearanceSync } from './appearance';
import { fetchHealth, startHeartbeat } from './heartbeat';
import { usePath } from './router';
import type { SessionState } from './session';
import { AppShell, type ServerState } from './shell/AppShell';
import { ProjectTrust } from './shell/ProjectTrust';
import { SessionNotice } from './shell/SessionNotice';
import { ConnectionsView } from './views/connections/ConnectionsView';
import { MigrationsView } from './views/migrations/MigrationsView';
import { NotFoundView, SettingsView, StartView } from './views/Views';
import { EMPTY_WORKSPACE, WorkspaceContext, type Workspace } from './workspace';

export type AppSession = SessionState | 'ended';

/** The SQL editor (STU-012) and CodeMirror load only when the page is opened: the other views stay small. */
const EditorView = lazy(() =>
  import('./views/editor/EditorView').then((module) => ({ default: module.EditorView })),
);

function useServer(fetcher: typeof fetch): [ServerState, () => void] {
  const [server, setServer] = useState<ServerState>({ kind: 'loading' });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    void fetchHealth(fetcher).then((health) => {
      if (active) setServer(health === null ? { kind: 'offline' } : { kind: 'ok', health });
    });
    return () => {
      active = false;
    };
  }, [fetcher, attempt]);
  return [server, () => setAttempt((n) => n + 1)];
}

function View({ path, fetcher }: { path: string; fetcher: typeof fetch }) {
  switch (path) {
    case '/':
      return <StartView />;
    case '/connections':
      return <ConnectionsView fetcher={fetcher} />;
    case '/sql':
      return (
        <Suspense fallback={null}>
          <EditorView fetcher={fetcher} />
        </Suspense>
      );
    case '/migrations':
      return <MigrationsView fetcher={fetcher} />;
    case '/settings':
      return <SettingsView fetcher={fetcher} />;
    default:
      return <NotFoundView />;
  }
}

const PAGE_TITLES: Record<string, string> = {
  '/connections': 'connections.title',
  '/sql': 'editor.title',
  '/migrations': 'migrations.title',
  '/settings': 'settings.title',
};

/**
 * The page: the shell with a session, otherwise the sign-in notice (STU-003). While the tab is open it sends the
 * heartbeat; when the server no longer knows the session (review S-1), the heartbeat stops and the page says so.
 */
export function App({
  fetcher = fetch,
  session = 'ok',
  workspace = EMPTY_WORKSPACE,
  heartbeat = true,
}: {
  fetcher?: typeof fetch;
  session?: SessionState;
  workspace?: Workspace;
  heartbeat?: boolean;
}) {
  const { t } = useTranslation();
  const path = usePath();
  const [server, refreshServer] = useServer(fetcher);
  const [ended, setEnded] = useState(false);
  const [offline, setOffline] = useState(false);
  const wasOffline = useRef(false);

  useEffect(() => {
    if (session !== 'ok' || !heartbeat) return undefined;
    return startHeartbeat(fetcher, (result) => {
      if (result === 'ended') setEnded(true);
      // Back after an outage (for example a sleeping laptop): read the server version again.
      if (wasOffline.current && result === 'ok') refreshServer();
      wasOffline.current = result === 'offline';
      setOffline(result === 'offline');
    });
    // refreshServer only bumps a counter; it does not need to restart the heartbeat.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetcher, session, heartbeat]);

  // Language, theme and density from settings.toml (TD-021): the browser forgets them, the port changes per start.
  useEffect(
    () => (session === 'ok' ? startAppearanceSync(fetcher) : undefined),
    [fetcher, session],
  );

  const state: AppSession = ended ? 'ended' : session;
  if (state !== 'ok') {
    return <SessionNotice state={state} />;
  }
  const pageKey = PAGE_TITLES[path];
  const notFound = !['/', ...Object.keys(PAGE_TITLES)].includes(path);
  const pageTitle = notFound ? t('notFound.title') : pageKey ? t(pageKey) : null;
  return (
    <WorkspaceContext.Provider value={workspace}>
      <AppShell path={path} pageTitle={pageTitle} server={offline ? { kind: 'offline' } : server}>
        <View path={path} fetcher={fetcher} />
      </AppShell>
      <ProjectTrust fetcher={fetcher} />
    </WorkspaceContext.Provider>
  );
}
