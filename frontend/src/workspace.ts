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
 * An open connection whose database no longer answers, or that the server closed (TD-034, 08 section 5 "error
 * recovery"): the page shows the banner "Connection lost" with "Reconnect" until a connection of the same profile is
 * opened again.
 */
export interface LostConnection {
  connectionId: string;
  profileId: string;
  /** The profile's name as the page shows it (escaped). */
  name: string;
  environment: Environment | null;
  /** `db.connection`, `studio.connection_not_found` or `studio.connection_stale`. */
  code: string;
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

export const ENV_LABEL: Record<Environment, string> = { dev: 'DEV', test: 'TEST', prod: 'PROD' };
