/**
 * What the status displays show (08 section 3): the active connection, its environment and the open tabs.
 *
 * Nothing fills this yet: connections arrive with the connection registry and manager (STU-008, STU-009), editor
 * tabs with the SQL editor (STU-012). Until then the shell shows "No connection"; the tests and the e2e harness
 * (src/harness/) pass a workspace to check the displays, in particular the PROD marking in the header, on every tab
 * and in the window title (STU-051).
 */
import { createContext, useContext } from 'react';

export type Environment = 'dev' | 'test' | 'prod';
export type ConnectionStatus = 'connected' | 'connecting' | 'disconnected' | 'error';

export interface ActiveConnection {
  name: string;
  environment: Environment;
  status: ConnectionStatus;
  latencyMs?: number;
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

export const ENV_LABEL: Record<Environment, string> = { dev: 'DEV', test: 'TEST', prod: 'PROD' };
