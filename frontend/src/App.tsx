import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from 'react';
import { useTranslation } from 'react-i18next';

import { checkConnection } from './api/connections';
import { startAppearanceSync } from './appearance';
import { ConnectionLoss } from './connectionLoss';
import { watchConnections } from './connectionWatch';
import { fetchHealth, startHeartbeat } from './heartbeat';
import { usePath } from './router';
import type { SessionState } from './session';
import { AppShell, type ServerState } from './shell/AppShell';
import { ConnectionLost } from './shell/ConnectionLost';
import { ProjectTrust } from './shell/ProjectTrust';
import { SessionNotice } from './shell/SessionNotice';
import { ConnectionsView } from './views/connections/ConnectionsView';
import { ExplorerView } from './views/explorer/ExplorerView';
import { DiagnosticsView } from './views/diagnostics/DiagnosticsView';
import { MigrationsView } from './views/migrations/MigrationsView';
import { NotFoundView, SettingsView, StartView } from './views/Views';
import {
  ConnectionSessionContext,
  WorkspaceContext,
  type ActiveConnection,
  prodKey,
  type ConnectionSession,
  type LostConnection,
  type Workspace,
} from './workspace';

export type AppSession = SessionState | 'ended';

/**
 * How often the page asks whether the database still answers on the header's connection (TD-034), while the tab is
 * visible; also when the tab becomes visible again.
 */
export const CONNECTION_CHECK_MS = 30_000;

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
    case '/explorer':
      return <ExplorerView fetcher={fetcher} />;
    case '/sql':
      return (
        <Suspense fallback={null}>
          <EditorView fetcher={fetcher} />
        </Suspense>
      );
    case '/migrations':
      return <MigrationsView fetcher={fetcher} />;
    case '/diagnostics':
      return <DiagnosticsView fetcher={fetcher} />;
    case '/settings':
      return <SettingsView fetcher={fetcher} />;
    default:
      return <NotFoundView />;
  }
}

const PAGE_TITLES: Record<string, string> = {
  '/connections': 'connections.title',
  '/explorer': 'explorer.title',
  '/sql': 'editor.title',
  '/migrations': 'migrations.title',
  '/diagnostics': 'diagnostics.title',
  '/settings': 'settings.title',
};

/**
 * The page: the shell with a session, otherwise the sign-in notice (STU-003). While the tab is open it sends the
 * heartbeat; when the server no longer knows the session (review S-1), the heartbeat stops and the page says so.
 */
export function App({
  fetcher = fetch,
  session = 'ok',
  workspace,
  heartbeat = true,
  connectionCheckMs = CONNECTION_CHECK_MS,
}: {
  fetcher?: typeof fetch;
  session?: SessionState;
  /** A fixed workspace (tests, e2e harness); without one the views fill it when they open a connection. */
  workspace?: Workspace;
  heartbeat?: boolean;
  /** The interval of the connection check (TD-034); 0 turns it off. */
  connectionCheckMs?: number;
}) {
  const { t } = useTranslation();
  const path = usePath();
  const [server, refreshServer] = useServer(fetcher);
  const [ended, setEnded] = useState(false);
  const [offline, setOffline] = useState(false);
  const wasOffline = useRef(false);
  const [active, setHeader] = useState<ActiveConnection | null>(null);
  /** The header's version: every change of `active` through the session and every connect request counts. */
  const headerChanges = useRef(0);
  const setActive = useCallback<Dispatch<SetStateAction<ActiveConnection | null>>>((next) => {
    headerChanges.current += 1;
    setHeader(next);
  }, []);
  /** D-02 confirmations: `prodKey` -> profile id. */
  const [confirmed] = useState(() => new Map<string, string>());
  const requests = useRef(0);
  // TD-034: the lost connections (the banner), kept by `loss`; the header's connection as of now for their names.
  const [lost, setLost] = useState<LostConnection[]>([]);
  const [loss] = useState(() => new ConnectionLoss());
  useEffect(
    () =>
      loss.attach({
        changed: setLost,
        forgetProd: (profileId) => {
          for (const [key, id] of [...confirmed]) if (id === profileId) confirmed.delete(key);
        },
        disconnected: (connectionId) =>
          setActive((current) =>
            current?.connectionId === connectionId
              ? { ...current, status: 'disconnected' }
              : current,
          ),
        header: () => active,
      }),
    [active, confirmed, loss, setActive],
  );
  // The functions keep their identity when the header changes, so views that depend on them do not reload.
  const sessionFunctions = useMemo<Omit<ConnectionSession, 'active' | 'setActive' | 'lost'>>(
    () => ({
      prodConfirmed: (target) => confirmed.has(prodKey(target)),
      confirmProd: (target) => {
        confirmed.set(prodKey(target), target.id);
      },
      forgetProd: (matches) => {
        for (const [key, id] of [...confirmed]) if (matches(id)) confirmed.delete(key);
      },
      beginConnect: () => {
        headerChanges.current += 1;
        return ++requests.current;
      },
      isLatestConnect: (request) => request === requests.current,
      headerVersion: () => headerChanges.current,
      markLost: (connectionId, code) => loss.markLost(connectionId, code),
      onReconnected: (listener) => loss.onReconnected(listener),
    }),
    [confirmed, loss],
  );

  // Every view reads through this fetcher, so every answer about a connection reaches the session (TD-034).
  const watched = useMemo(
    () =>
      watchConnections(fetcher, {
        lost: (connectionId, code) => loss.markLost(connectionId, code),
        opened: (connection) => loss.seen(connection, true),
        listed: (list) => {
          for (const connection of list) loss.seen(connection, false);
        },
      }),
    [fetcher, loss],
  );
  const connections = useMemo<ConnectionSession>(
    () => ({ active, setActive, lost, ...sessionFunctions }),
    [active, setActive, lost, sessionFunctions],
  );

  // TD-034: ask now and then whether the database still answers on the header's connection; a failed check reaches
  // `markLost` through the watched fetcher.
  const checked = active?.status === 'connected' ? (active.connectionId ?? null) : null;
  useEffect(() => {
    if (checked === null || connectionCheckMs <= 0 || session !== 'ok') return undefined;
    const check = () => {
      if (document.visibilityState === 'hidden') return;
      void checkConnection(watched, checked);
    };
    const timer = window.setInterval(check, connectionCheckMs);
    const onVisible = () => {
      if (document.visibilityState === 'visible') check();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [checked, connectionCheckMs, session, watched]);
  const shown = useMemo<Workspace>(
    () => workspace ?? { connection: active, tabs: [], activeTab: null },
    [workspace, active],
  );

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
    <WorkspaceContext.Provider value={shown}>
      <ConnectionSessionContext.Provider value={connections}>
        <AppShell
          path={path}
          pageTitle={pageTitle}
          server={offline ? { kind: 'offline' } : server}
          banner={<ConnectionLost fetcher={watched} />}
        >
          <View path={path} fetcher={watched} />
        </AppShell>
        <ProjectTrust fetcher={watched} />
      </ConnectionSessionContext.Provider>
    </WorkspaceContext.Provider>
  );
}
