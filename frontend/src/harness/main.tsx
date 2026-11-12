/**
 * E2E harness (development server only; `vite build` builds index.html alone, so this page is never in the wheel).
 *
 * It renders the real App with a fake local server and a workspace, so e2e/ can check states that the backend cannot
 * produce yet: a PROD connection with tabs (STU-051), each dialog, the sign-in notices and the ended session
 * (review S-1), and the notices of project trust (review I-2 of stu-006-review.md). Query parameters: `scenario`
 * (start, prod, test, dialog-d01, dialog-d01-hostile, dialog-d01-stale, dialog-d02, dialog-d06, dialog-d08, ended,
 * used, project-invalid, connections: profiles of both groups, the connection manager's writes answered, a connection
 * test and opening a connection answered (STU-008; parts-pg's test fails with `db.connection`);
 * migrations: the code trust and migration status of STU-015; dialog-trust-code; diagnostics: the findings of
 * STU-014, `?slow=1` keeps a run open until it is canceled; explorer: the catalogs and details of STU-010), `path`
 * (/, /connections, /explorer, /migrations, /diagnostics, /settings), `theme` (light, dark) and `lang` (en, de).
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
import type { Workspace } from '../workspace';
import { explorerAnswer } from './explorer';
import styles from './harness.module.css';
import { diagnosis } from './diagnostics';
import { codeTrust, migrationStatus } from './migrations';
import { PROFILES, SETTINGS, TEST_RESULTS } from './profiles';
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

/** The writes the page sent in the connections, migrations, diagnostics and explorer scenarios (e2e/*.spec.ts). */
const sent = window as unknown as {
  writes: { method: string; url: string; contentType: string }[];
};
sent.writes = [];

/** The connections the connections scenario opened, by id. */
const openConnections = new Map<string, Record<string, unknown>>();

/**
 * Profiles another tab made PROD (TD-038 flip scenario): `window.harnessMakeProd(id)` changes the profile as the
 * server would read it from now on, without telling the page.
 */
const madeProd = new Set<string>();
(window as unknown as { harnessMakeProd: (id: string) => void }).harnessMakeProd = (id) => {
  madeProd.add(id);
};

function currentProfiles(): typeof PROFILES {
  return {
    ...PROFILES,
    profiles: PROFILES.profiles.map((item) =>
      madeProd.has(item.id) ? { ...item, environment: 'prod' as const } : item,
    ),
  };
}

/** The connection test and "Connect" of the connections scenario (STU-008). */
function connectionAnswer(method: string, url: string, body: string): Response {
  const doc = JSON.parse(body) as {
    profile_id?: string;
    connection_id?: string;
    confirmed_environment?: string;
  };
  if (method === 'DELETE') {
    openConnections.delete(doc.connection_id ?? '');
    return new Response(null, { status: 204 });
  }
  const profile = currentProfiles().profiles.find((item) => item.id === doc.profile_id);
  // D-02 on the server (TD-038): a PROD profile needs a request that confirmed PROD.
  if (profile?.environment === 'prod' && doc.confirmed_environment !== 'prod') {
    return new Response(
      JSON.stringify({ code: 'studio.prod_confirmation_required', kind: 'database', status: 409 }),
      { status: 409 },
    );
  }
  if (profile === undefined || profile.id === 'project:parts-pg') {
    return new Response(
      JSON.stringify({ code: 'db.connection', kind: 'connection', status: 502, sqlstate: '08001' }),
      { status: 502 },
    );
  }
  if (url === '/api/connection-tests') {
    return new Response(JSON.stringify(TEST_RESULTS[profile.engine]), { status: 200 });
  }
  const id = `c-${profile.name}`;
  const opened = {
    connection_id: id,
    profile_id: profile.id,
    engine: profile.engine,
    environment: profile.environment,
    read_only: true,
    url: 'postgresql://studio_ro:***@measure.example.internal/measurements',
    opened_at: 1,
  };
  openConnections.set(id, opened);
  return new Response(JSON.stringify(opened), { status: 201 });
}

/** The diagnostics scenario with `slow=1`: a run answers only after the page canceled it (e2e/diagnostics.spec.ts). */
const slow = params.get('slow') === '1';
let cancelRun: (() => void) | null = null;

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
      if (url === '/api/connection-tests' || url === '/api/connections') {
        return connectionAnswer(method, url, String(init?.body ?? '{}'));
      }
      return new Response(JSON.stringify({ id: 'user:new', digest: 'c'.repeat(64) }), {
        status: method === 'POST' ? 201 : 200,
      });
    }
    if (url === '/api/profiles') {
      return new Response(JSON.stringify(currentProfiles()), { status: 200 });
    }
    if (url === '/api/connections') {
      return new Response(JSON.stringify({ connections: [...openConnections.values()] }), {
        status: 200,
      });
    }
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
  if (scenario === 'diagnostics') {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (method !== 'GET') {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      sent.writes.push({ method, url, contentType: headers['Content-Type'] ?? '' });
    }
    if (url === '/api/profiles') return new Response(JSON.stringify(PROFILES), { status: 200 });
    if (url === '/api/connections' && method === 'POST') {
      return new Response(JSON.stringify({ connection_id: 'c1' }), { status: 201 });
    }
    if (url === '/api/connections/c1/diagnostics/cancel') {
      const running = cancelRun;
      cancelRun = null;
      running?.();
      return new Response(JSON.stringify({ canceled: running !== null }), { status: 202 });
    }
    if (url === '/api/connections/c1/diagnostics') {
      if (slow) {
        await new Promise<void>((resolve) => {
          cancelRun = resolve;
        });
        return new Response(JSON.stringify(diagnosis(true)), { status: 200 });
      }
      return new Response(JSON.stringify(diagnosis()), { status: 200 });
    }
  }
  if (scenario === 'explorer') {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (method !== 'GET') {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      sent.writes.push({ method, url, contentType: headers['Content-Type'] ?? '' });
    }
    if (url === '/api/profiles') return new Response(JSON.stringify(PROFILES), { status: 200 });
    const answer = explorerAnswer(url, method, typeof init?.body === 'string' ? init.body : '');
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
      workspace={WORKSPACES[scenario === 'dialog-d02' ? 'prod' : scenario]}
    />
    {scenario.startsWith('dialog-') ? <HarnessDialog id={scenario} /> : null}
  </StrictMode>,
);
