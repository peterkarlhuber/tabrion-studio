/**
 * What the status displays show (08 section 3): the active connection, its environment and the open tabs.
 *
 * The connection manager (STU-008) and the migration status view fill the active connection when they open one
 * (`ConnectionSession`); editor tabs arrive with the SQL editor (STU-012). The tests and the e2e harness (src/harness/)
 * can pass a whole workspace to check the displays, in particular the PROD marking in the header, on every tab and in
 * the window title (STU-051).
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  type Dispatch,
  type SetStateAction,
} from 'react';

export type Environment = 'dev' | 'test' | 'prod';
export type ConnectionStatus = 'connected' | 'connecting' | 'disconnected' | 'error';

export interface ActiveConnection {
  /** The profile's name as the page shows it (escaped, `lib/visible.ts`). */
  name: string;
  environment: Environment;
  status: ConnectionStatus;
  latencyMs?: number;
  /** The open connection (`POST /api/connections`); absent while it is being opened. */
  connectionId?: string;
  profileId?: string;
}

export interface WorkspaceTab {
  id: string;
  title: string;
  /** The connection the tab belongs to; tabs can belong to different connections (08 section 4.4). */
  connection: string;
  environment: Environment;
}

export interface Workspace {
  connection: ActiveConnection | null;
  tabs: WorkspaceTab[];
  activeTab: string | null;
}

export const EMPTY_WORKSPACE: Workspace = { connection: null, tabs: [], activeTab: null };

export const WorkspaceContext = createContext<Workspace>(EMPTY_WORKSPACE);

export function useWorkspace(): Workspace {
  return useContext(WorkspaceContext);
}

/** What D-02 named: a profile and the database it points at (C-1 of `stu-008-connect-review.md`). */
export interface ProdTarget {
  id: string;
  environment: Environment;
  engine: string;
  host?: string;
  port?: number;
  database?: string;
  user?: string;
  path?: string;
}

/** The key of a D-02 confirmation: the profile id and its whole target, so a changed target asks again. */
export function prodKey(target: ProdTarget): string {
  return JSON.stringify([
    target.id,
    target.environment,
    target.engine,
    target.host ?? null,
    target.port ?? null,
    target.database ?? null,
    target.user ?? null,
    target.path ?? null,
  ]);
}

/**
 * Why a connection is gone (QA-S1 S1-32, owner decision of 2026-11-14 14:13Z): `lost` when its database stopped
 * answering or the server closed it (TD-034), `disconnected` when the user chose "Disconnect" in this page. After a
 * loss the SQL editor opens a DEV profile again by itself; after the user's own disconnect nothing opens a connection
 * of that profile until the user chooses "Reconnect" (or connects it again in the connection manager or the explorer).
 */
export type GoneCause = 'lost' | 'disconnected';

/**
 * An open connection whose database no longer answers, that the server closed (TD-034, 08 section 5 "error
 * recovery"), or that the user disconnected and a run asked for again (S1-32): the page shows the banner "Connection
 * lost" with "Reconnect" until a connection of the same profile is opened again.
 */
export interface LostConnection {
  connectionId: string;
  profileId: string;
  /** The profile's name as the page shows it (escaped). */
  name: string;
  environment: Environment | null;
  cause: GoneCause;
  /**
   * `db.connection`, `studio.connection_not_found` or `studio.connection_stale`; null for the user's own disconnect
   * when no request on the connection has failed.
   */
  code: string | null;
}

/** The connection the user disconnected in this page (S1-32): what the page showed for it. */
export interface DisconnectedConnection {
  connectionId: string;
  profileId: string;
  /** The profile's name as the page shows it (escaped). */
  name: string;
  environment: Environment | null;
}

/** A lost connection was replaced: views that used `from` go on with `to` (the same id if the database came back). */
export interface Reconnected {
  profileId: string;
  from: string;
  to: string;
}

/**
 * The page's connection state, shared by the views that open a connection (STU-008): the connection the header
 * shows, and the PROD databases the user already confirmed with D-02 in this page session (08 section 6: "the first
 * time per session"). A confirmation belongs to the profile id and its target (`prodKey`): a profile that now points
 * elsewhere asks again, and saving, deleting or trusting profiles again forgets theirs (`forgetProd`). A reload starts
 * a new page session, so D-02 is asked again. Without a provider (a view tested on its own) nothing is kept and D-02
 * is asked every time.
 *
 * `beginConnect` numbers the connect requests: only the newest may still set the header (`isLatestConnect`).
 * `headerVersion` counts the header's changes: a list of open connections read before a change is outdated.
 */
export interface ConnectionSession {
  active: ActiveConnection | null;
  setActive: Dispatch<SetStateAction<ActiveConnection | null>>;
  prodConfirmed: (target: ProdTarget) => boolean;
  confirmProd: (target: ProdTarget) => void;
  /** Forget the confirmations of every profile id `matches` accepts. */
  forgetProd: (matches: (profileId: string) => boolean) => void;
  beginConnect: () => number;
  isLatestConnect: (request: number) => boolean;
  /**
   * Counts every change of the header through `setActive` and every connect request. A list of open connections read
   * while it had another value may miss the header's connection, so it must not clear it.
   */
  headerVersion: () => number;
  /** The connections that are lost now, oldest first (TD-034). */
  lost: LostConnection[];
  /**
   * Report that `connectionId` is lost (a request on it answered `code`). An id the page never opened, or one already
   * replaced, is ignored. A PROD profile's D-02 confirmation is forgotten, so "Reconnect" asks D-02 again.
   */
  markLost: (connectionId: string, code: string) => void;
  /** Listen for replaced connections; returns the function that stops listening. */
  onReconnected: (listener: (event: Reconnected) => void) => () => void;
  /**
   * The user chose "Disconnect" for `connection` (S1-32). Until a connection of its profile is opened again, nothing
   * may open one by itself: `isDisconnected` says so. "Reconnect" on the banner asks D-02 again for a PROD profile.
   */
  disconnect: (connection: DisconnectedConnection) => void;
  /** Whether the user disconnected `profileId` in this page and no connection of it was opened since. */
  isDisconnected: (profileId: string) => boolean;
  /**
   * A run needs `profileId`, which the user disconnected: show the banner with "Reconnect" for it. `resume` runs with
   * the new connection once "Reconnect" on the banner opened it (after D-02 for PROD), never otherwise; a later call
   * for the same profile replaces it. Returns the function that drops `resume` (a view that goes calls it).
   */
  askReconnect: (profileId: string, resume: (connectionId: string) => void) => () => void;
  /** "Reconnect" on the banner opened `connectionId` for `profileId`: run what waited for it (`askReconnect`). */
  reconnected: (profileId: string, connectionId: string) => void;
}

export const NO_CONNECTION_SESSION: ConnectionSession = {
  active: null,
  setActive: () => undefined,
  prodConfirmed: () => false,
  confirmProd: () => undefined,
  forgetProd: () => undefined,
  beginConnect: () => 0,
  isLatestConnect: () => true,
  headerVersion: () => 0,
  lost: [],
  markLost: () => undefined,
  onReconnected: () => () => undefined,
  disconnect: () => undefined,
  isDisconnected: () => false,
  askReconnect: () => () => undefined,
  reconnected: () => undefined,
};

export const ConnectionSessionContext = createContext<ConnectionSession>(NO_CONNECTION_SESSION);

export function useConnectionSession(): ConnectionSession {
  return useContext(ConnectionSessionContext);
}

/**
 * Call `listener` when a lost connection is replaced (TD-034): a view that read on `event.from` goes on with
 * `event.to`. The newest `listener` is called; it need not be stable.
 */
export function useReconnected(listener: (event: Reconnected) => void): void {
  const { onReconnected } = useConnectionSession();
  const current = useRef(listener);
  useEffect(() => {
    current.current = listener;
  });
  useEffect(() => onReconnected((event) => current.current(event)), [onReconnected]);
}

/**
 * The rule of QA-S1 S1-32 (owner decision of 2026-11-14 14:13Z) for a view whose button opens a connection ("Show
 * status", "Run diagnosis", "Inspect"): `hold(profileId, proceed)` returns true when the user disconnected
 * `profileId` in this page. Then nothing is opened: the banner asks with "Reconnect", and `proceed` runs once
 * "Reconnect" there opened the connection again (D-02 first for PROD). A newer press drops the earlier wait, and so
 * does leaving the view. "Open" in the explorer and "Connect" in the connection manager connect explicitly and do not
 * ask.
 */
export function useReconnectGate(): (profileId: string, proceed: () => void) => boolean {
  const { isDisconnected, askReconnect } = useConnectionSession();
  const drop = useRef<(() => void) | null>(null);
  useEffect(
    () => () => {
      drop.current?.();
      drop.current = null;
    },
    [],
  );
  return useCallback(
    (profileId, proceed) => {
      drop.current?.();
      drop.current = null;
      if (!isDisconnected(profileId)) return false;
      drop.current = askReconnect(profileId, () => {
        drop.current = null;
        proceed();
      });
      return true;
    },
    [askReconnect, isDisconnected],
  );
}

export const ENV_LABEL: Record<Environment, string> = { dev: 'DEV', test: 'TEST', prod: 'PROD' };
