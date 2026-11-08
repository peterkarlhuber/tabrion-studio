/**
 * E2E harness (development server only; `vite build` builds index.html alone, so this page is never in the wheel).
 *
 * It renders the real App with a fake local server and a workspace, so e2e/ can check states that the backend cannot
 * produce yet: a PROD connection with tabs (STU-051), each dialog, the sign-in notices and the ended session
 * (review S-1). Query parameters: `scenario` (start, prod, test, dialog-d01, dialog-d02, dialog-d06, dialog-d08,
 * ended, used), `path` (/, /connections, /settings), `theme` (light, dark) and `lang` (en, de).
 */
import { StrictMode, useState, type ReactElement } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from '../App';
import {
  ProdConnectDialog,
  QuitDialog,
  TrustProjectDialog,
  UnsavedScriptDialog,
} from '../components/dialogs/StudioDialogs';
import { setLocale } from '../i18n';
import type { SessionState } from '../session';
import '../styles/global.css';
import { initTheme, setThemePreference } from '../theme';
import { EMPTY_WORKSPACE, type Workspace } from '../workspace';
import styles from './harness.module.css';

const params = new URLSearchParams(window.location.search);
const scenario = params.get('scenario') ?? 'start';
const theme = params.get('theme');
const lang = params.get('lang');

window.history.replaceState(null, '', params.get('path') ?? '/');
initTheme();
if (theme === 'light' || theme === 'dark') setThemePreference(theme);
if (lang === 'en' || lang === 'de') void setLocale(lang);

/** Heartbeats the page has sent (e2e/status.spec.ts checks that they stop after the session ended). */
const counters = window as unknown as { heartbeats: number };
counters.heartbeats = 0;

const fakeServer: typeof fetch = async (input) => {
  if (String(input) === '/api/heartbeat') {
    counters.heartbeats += 1;
    return scenario === 'ended'
      ? new Response(JSON.stringify({ code: 'access.session_required' }), { status: 401 })
      : new Response(null, { status: 204 });
  }
  return new Response(JSON.stringify({ status: 'ok', version: '0.1.0.dev0' }), { status: 200 });
};

const WORKSPACES: Record<string, Workspace> = {
  prod: {
    connection: {
      name: 'measure-central',
      environment: 'prod',
      status: 'connected',
      latencyMs: 12,
    },
    tabs: [
      { id: 'q1', title: 'Query 1', connection: 'measure-central', environment: 'prod' },
      { id: 'q2', title: 'parts-dev', connection: 'parts-dev', environment: 'dev' },
      { id: 'q3', title: 'parts-pg', connection: 'parts-pg', environment: 'test' },
    ],
    activeTab: 'q1',
  },
  test: {
    connection: { name: 'parts-pg', environment: 'test', status: 'connecting' },
    tabs: [{ id: 'q1', title: 'Query 1', connection: 'parts-pg', environment: 'test' }],
    activeTab: 'q1',
  },
};

function HarnessDialog({ id }: { id: string }) {
  const [open, setOpen] = useState(true);
  const [last, setLast] = useState('none');
  const done = (choice: string) => () => {
    setOpen(false);
    setLast(choice);
  };
  const dialogs: Record<string, ReactElement> = {
    'dialog-d01': (
      <TrustProjectDialog
        open={open}
        file=".tabrion/studio.toml"
        connections={['parts-dev', 'parts-pg', 'measure-central']}
        hosts={['db.internal']}
        files={['./secrets/db']}
        onTrust={done('trust')}
        onReject={done('cancel')}
      />
    ),
    'dialog-d02': (
      <ProdConnectDialog
        open={open}
        name="measure-central"
        timeoutSeconds={15}
        onConnect={done('connect')}
        onCancel={done('cancel')}
      />
    ),
    'dialog-d06': (
      <UnsavedScriptDialog
        open={open}
        name="Query 2"
        onSave={done('save')}
        onDiscard={done('discard')}
        onCancel={done('cancel')}
      />
    ),
    'dialog-d08': (
      <QuitDialog open={open} running={2} onQuit={done('quit')} onCancel={done('cancel')} />
    ),
  };
  return (
    <aside className={styles.harness} aria-label="E2E harness">
      <button type="button" data-testid="opener" onClick={() => setOpen(true)}>
        Open dialog
      </button>
      <output data-testid="choice">{last}</output>
      {dialogs[id]}
    </aside>
  );
}

const root = document.getElementById('root');
if (root === null) throw new Error('#root is missing');
const session: SessionState = scenario === 'used' ? 'used' : 'ok';
createRoot(root).render(
  <StrictMode>
    <App
      fetcher={fakeServer}
      session={session}
      workspace={WORKSPACES[scenario === 'dialog-d02' ? 'prod' : scenario] ?? EMPTY_WORKSPACE}
    />
    {scenario.startsWith('dialog-') ? <HarnessDialog id={scenario} /> : null}
  </StrictMode>,
);
