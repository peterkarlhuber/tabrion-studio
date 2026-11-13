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
 * STU-014, `?slow=1` keeps a run open until it is canceled, `?report=unavailable` refuses the report of TD-036;
 * editor: the profiles and a fake query engine for the SQL editor of STU-012, with `sql` as the first tab's draft,
 * `profile` its profile, `maxRows` the row limit setting and `connectDelay` a delay of the connection; explorer: the
 * catalogs and details of STU-010 and the data viewer's pages of STU-013), `path` (/, /connections, /explorer, /sql,
 * /history, /migrations, /diagnostics, /settings). In every scenario the fake answers STU-020's routes
 * (`history.ts`): the query history (`historyOff=1` turns it off), favorites (`favorites`, `recent`: profile ids
 * separated by commas), the project's scripts (`sql/report.sql`, `sql/parts.sql`) and the catalog for completion,
 * `theme` (light, dark) and `lang` (en, de). The editor's drafts (TD-033) go to a fake `/api/drafts` (`drafts.ts`) that
 * keeps them between page loads of the harness; `legacyDraft` puts a draft into `localStorage` the way an earlier
 * version did (the page moves it to the server and removes it) and `draftsOff=1` answers `studio.state_unavailable`.
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
import { createViewer } from './viewer';
import styles from './harness.module.css';
import { REPORT_FILENAME, REPORT_MARKDOWN, diagnosis } from './diagnostics';
import { codeTrust, migrationStatus } from './migrations';
import { draftsKey } from '../views/editor/drafts';
import { createDraftsServer } from './drafts';
import { createHistoryServer } from './history';
import { createQueryServer } from './queries';
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

/** The fake query engine of the editor scenario; e2e/editor.spec.ts reads what the page sent (`window.queryLog`). */
/** The data viewer of the explorer scenario (STU-013 page); e2e reads what the page asked for. */
const viewer = createViewer();
(window as unknown as { viewerLog: unknown }).viewerLog = viewer.log;
// `connectDelay` (ms) delays opening a connection (e2e/editor.spec.ts: run, cancel and close while connecting);
// `startDelay` (ms) delays the answer to starting a query (a cancel while the start request is on its way).
const queryServer = createQueryServer({
  connectDelayMs: Number(params.get('connectDelay') ?? 0),
  startDelayMs: Number(params.get('startDelay') ?? 0),
});
(window as unknown as { queryLog: unknown }).queryLog = queryServer.log;
/** STU-020's routes; e2e reads what the page sent (`window.historyLog`). */
const listParam = (name: string) => (params.get(name) ?? '').split(',').filter((id) => id !== '');
const historyServer = createHistoryServer({
  disabled: params.get('historyOff') === '1',
  favorites: listParam('favorites'),
  recent: listParam('recent'),
  scripts: {
    'sql/report.sql': 'select id, label\nfrom parts\nwhere id = :id;\n',
    'sql/parts.sql': 'select count(*) from parts;\n',
  },
});
(window as unknown as { historyLog: unknown }).historyLog = historyServer.log;
const draft = params.get('sql');
/** The project folder of the editor scenario: its drafts are stored under that project's key (L-5). */
const EDITOR_PROJECT = '/home/demo/parts';
const draftsServer = createDraftsServer({
  storage: window.sessionStorage,
  unavailable: params.get('draftsOff') === '1',
  ...(scenario === 'editor' && draft !== null
    ? {
        seed: {
          tabs: [
            {
              id: 'd1',
              title: 'Query 1',
              profile_id: params.get('profile') ?? 'project:parts-dev',
              sql: draft,
              saved_sql: '',
            },
          ],
          active_tab: 'd1',
        },
      }
    : {}),
});
(window as unknown as { draftsLog: unknown }).draftsLog = draftsServer.log;
/** A draft as an earlier version kept it in this browser's storage (the TD-033 migration). */
const legacyDraft = params.get('legacyDraft');
if (scenario === 'editor' && legacyDraft !== null) {
  window.localStorage.setItem(
    draftsKey(EDITOR_PROJECT),
    JSON.stringify({
      tabs: [
        {
          id: 'l1',
          title: 'Old draft',
          profile_id: 'project:parts-dev',
          sql: legacyDraft,
          saved_sql: '',
        },
      ],
      activeTab: 'l1',
    }),
  );
}
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
/** The diagnostics scenario with `report=unavailable`: the report is refused with `studio.report_unavailable`. */
const reportUnavailable = params.get('report') === 'unavailable';
let cancelRun: (() => void) | null = null;

/** Whether the migrations scenario's project code is trusted (the trust dialog and "Stop trusting" change it). */
let codeTrusted = false;

/**
 * TD-034, the lost connection: `window.harnessLose(id)` makes every request on the connection `id` answer
 * `db.connection` (the database does not answer), its health check included, until the page opens a connection of
 * that profile again. `check=<ms>` sets the page's connection check interval (off by default in the harness).
 */
const lostConnections = new Set<string>();
(window as unknown as { harnessLose: (id: string) => void }).harnessLose = (id) => {
  lostConnections.add(id);
};
const connectionCheckMs = Number(params.get('check') ?? '0');

function lostAnswer(url: string, method: string): Response | null {
  if (url === '/api/connections' && method === 'POST') {
    lostConnections.clear();
    return null;
  }
  const match = /^\/api\/connections\/([^/?]+)\/([a-z-]+)/.exec(url);
  if (match === null) return null;
  const [, id = '', route] = match;
  if (lostConnections.has(id)) {
    return new Response(
      JSON.stringify({ code: 'db.connection', kind: 'connection', status: 502, sqlstate: '08006' }),
      { status: 502 },
    );
  }
  if (route === 'health' && method === 'GET') {
    return new Response(JSON.stringify({ state: 'open', latency_ms: 1.2 }), { status: 200 });
  }
  return null;
}

const fakeServer: typeof fetch = async (input, init) => {
  const lost = lostAnswer(String(input), init?.method ?? 'GET');
  if (lost !== null) return lost;
  const drafts = draftsServer.handle(String(input), init);
  if (drafts !== null) return drafts;
  // STU-020's own routes, in every scenario (the catalog for completion comes after the scenarios' own).
  if (/^\/api\/(history|favorites|scripts)(\/|\?|$)/.test(String(input))) {
    const answer = historyServer.handle(String(input), init);
    if (answer !== null) return answer;
  }
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
  if (scenario === 'editor') {
    const url = String(input);
    if (url === '/api/profiles') return new Response(JSON.stringify(PROFILES), { status: 200 });
    if (url === '/api/project') {
      return new Response(JSON.stringify({ path: EDITOR_PROJECT, state: 'none' }), { status: 200 });
    }
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
    if (url === '/api/connections/c1/diagnostics/report') {
      if (reportUnavailable) {
        return new Response(
          JSON.stringify({ code: 'studio.report_unavailable', kind: 'database', status: 503 }),
          { status: 503 },
        );
      }
      return new Response(REPORT_MARKDOWN, {
        status: 200,
        headers: {
          'Content-Type': 'text/markdown; charset=utf-8',
          'Content-Disposition': `attachment; filename="${REPORT_FILENAME}"`,
        },
      });
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
    const page = method === 'GET' ? viewer.answer(url) : null;
    if (page !== null) return page;
    const answer = explorerAnswer(url, method, typeof init?.body === 'string' ? init.body : '');
    if (answer !== null) return answer;
  }
  const stu020 = historyServer.handle(String(input), init);
  if (stu020 !== null) return stu020;
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
      connectionCheckMs={connectionCheckMs}
    />
    {scenario.startsWith('dialog-') ? <HarnessDialog id={scenario} /> : null}
  </StrictMode>,
);
