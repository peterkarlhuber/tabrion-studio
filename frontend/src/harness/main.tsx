/**
 * E2E harness (development server only; `vite build` builds index.html alone, so this page is never in the wheel).
 *
 * It renders the real App with a fake local server and a workspace, so e2e/ can check states that the backend cannot
 * produce yet: a PROD connection with tabs (STU-051), each dialog, the sign-in notices and the ended session
 * (review S-1), and the notices of project trust (review I-2 of stu-006-review.md). Query parameters: `scenario`
 * (start, prod, test, dialog-d01, dialog-d01-hostile, dialog-d01-stale, dialog-d02, dialog-d06, dialog-d08, ended,
 * used, project-invalid, connections: profiles of both groups, the connection manager's writes answered, STU-008;
 * migrations: the code trust and migration status of STU-015; dialog-trust-code; editor: the profiles and a fake
 * query engine for the SQL editor of STU-012, with `sql` as the first tab's draft, `profile` its profile and
 * `maxRows` the row limit setting), `path` (/, /connections, /sql,
 * /migrations, /settings), `theme` (light, dark) and `lang` (en, de).
 */
import { StrictMode, useState, type ReactElement } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from '../App';
import {
  ProdConnectDialog,
  QuitDialog,
  TrustCodeDialog,
  TrustProjectDialog,
  UnsavedScriptDialog,
} from '../components/dialogs/StudioDialogs';
import { setLocale } from '../i18n';
import type { SessionState } from '../session';
import '../styles/global.css';
import { installTooltipDismiss } from '../lib/tooltips';
import { initTheme, setThemePreference } from '../theme';
import { EMPTY_WORKSPACE, type Workspace } from '../workspace';
import styles from './harness.module.css';
import { codeTrust, migrationStatus } from './migrations';
import { createQueryServer } from './queries';
import { PROFILES, SETTINGS } from './profiles';
import { DIFF, HOSTILE_REVIEW, INVALID_PROJECT, REVIEW } from './trust';

const params = new URLSearchParams(window.location.search);
const scenario = params.get('scenario') ?? 'start';
const theme = params.get('theme');
const lang = params.get('lang');

window.history.replaceState(null, '', params.get('path') ?? '/');
initTheme();
installTooltipDismiss();
if (theme === 'light' || theme === 'dark') setThemePreference(theme);
if (lang === 'en' || lang === 'de') void setLocale(lang);

/** Heartbeats the page has sent (e2e/status.spec.ts checks that they stop after the session ended). */
const counters = window as unknown as { heartbeats: number };
counters.heartbeats = 0;

/** The writes the page sent in the connections and migrations scenarios (e2e/connections.spec.ts, migrations.spec.ts). */
const sent = window as unknown as {
  writes: { method: string; url: string; contentType: string }[];
};
sent.writes = [];

/** The fake query engine of the editor scenario; e2e/editor.spec.ts reads what the page sent (`window.queryLog`). */
const queryServer = createQueryServer();
(window as unknown as { queryLog: unknown }).queryLog = queryServer.log;
const draft = params.get('sql');
if (scenario === 'editor' && draft !== null) {
  window.localStorage.setItem(
    'tabrion-studio.editor-drafts',
    JSON.stringify({
      tabs: [
        {
          id: 'd1',
          title: 'Query 1',
          profile_id: params.get('profile') ?? 'project:parts-dev',
          sql: draft,
          saved_sql: '',
        },
      ],
      activeTab: 'd1',
    }),
  );
}

/** Whether the migrations scenario's project code is trusted (the trust dialog and "Stop trusting" change it). */
let codeTrusted = false;

const fakeServer: typeof fetch = async (input, init) => {
  if (scenario === 'connections') {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (method !== 'GET') {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      sent.writes.push({ method, url, contentType: headers['Content-Type'] ?? '' });
      if (url.endsWith('/secret')) return new Response(null, { status: 204 });
      return new Response(JSON.stringify({ id: 'user:new', digest: 'c'.repeat(64) }), {
        status: method === 'POST' ? 201 : 200,
      });
    }
    if (url === '/api/profiles') return new Response(JSON.stringify(PROFILES), { status: 200 });
    if (url === '/api/secrets') {
      return new Response(JSON.stringify({ keyring: { available: true } }), { status: 200 });
    }
    if (url === '/api/settings') return new Response(JSON.stringify(SETTINGS), { status: 200 });
  }
  if (scenario === 'migrations') {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (method !== 'GET') {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      sent.writes.push({ method, url, contentType: headers['Content-Type'] ?? '' });
    }
    if (url === '/api/project/code-trust') {
      if (method === 'POST') codeTrusted = true;
      if (method === 'DELETE') codeTrusted = false;
      if (method !== 'GET') return new Response(null, { status: 204 });
      return new Response(JSON.stringify(codeTrust(codeTrusted)), { status: 200 });
    }
    if (url === '/api/profiles') return new Response(JSON.stringify(PROFILES), { status: 200 });
    if (url === '/api/connections' && method === 'POST') {
      return new Response(JSON.stringify({ connection_id: 'c1' }), { status: 201 });
    }
    if (url === '/api/connections/c1/migrations') {
      return new Response(JSON.stringify(migrationStatus(codeTrusted)), { status: 200 });
    }
  }
  if (scenario === 'editor') {
    const url = String(input);
    if (url === '/api/profiles') return new Response(JSON.stringify(PROFILES), { status: 200 });
    if (url === '/api/settings') {
      // `maxRows` sets the max_rows setting (the 100 000-row test of e2e/editor.spec.ts).
      const maxRows = Number(params.get('maxRows') ?? SETTINGS.settings.limits.max_rows);
      const limits = { ...SETTINGS.settings.limits, max_rows: maxRows };
      return new Response(
        JSON.stringify({ ...SETTINGS, settings: { ...SETTINGS.settings, limits } }),
        { status: 200 },
      );
    }
    const answer = queryServer.handle(url, init);
    if (answer !== null) return answer;
  }
  if (String(input) === '/api/heartbeat') {
    counters.heartbeats += 1;
    return scenario === 'ended'
      ? new Response(JSON.stringify({ code: 'access.session_required' }), { status: 401 })
      : new Response(null, { status: 204 });
  }
  if (String(input) === '/api/project' && scenario === 'project-invalid') {
    return new Response(JSON.stringify(INVALID_PROJECT), { status: 200 });
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
        review={REVIEW}
        onTrust={done('trust')}
        onReject={done('cancel')}
      />
    ),
    'dialog-d01-hostile': (
      <TrustProjectDialog
        open={open}
        file=".tabrion/studio.toml"
        review={HOSTILE_REVIEW}
        diff={DIFF}
        onTrust={done('trust')}
        onReject={done('cancel')}
      />
    ),
    'dialog-d01-stale': (
      <TrustProjectDialog
        open={open}
        file=".tabrion/studio.toml"
        review={REVIEW}
        diff={{ ...DIFF, unknown_keys: { added: ['profile[0].new'], removed: ['profile[0].old'] } }}
        stale
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
    'dialog-trust-code': (
      <TrustCodeDialog
        open={open}
        configFile="alembic.ini"
        scriptLocation="migrations"
        revisionFiles={['0001_parts.py', '0002_measurements.py', '0003_indexes.py']}
        onTrust={done('trust')}
        onReject={done('cancel')}
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
