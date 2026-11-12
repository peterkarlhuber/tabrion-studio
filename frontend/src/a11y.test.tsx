/**
 * STU-005 acceptance: axe finds no serious or critical violation in the shell, its views, the sign-in notices, the
 * status displays and every dialog. This runs in jsdom on every CI run (`npm test`); jsdom has no layout, so axe
 * cannot judge colour contrast here. e2e/a11y.spec.ts runs the full rule set, colour contrast included, in Chromium
 * in the light and the dark theme.
 */
import { act, cleanup, render } from '@testing-library/react';
import axe from 'axe-core';
import type { ReactElement } from 'react';

import { App } from './App';
import {
  ProdConnectDialog,
  QuitDialog,
  TrustCodeDialog,
  TrustProjectDialog,
  UnsavedScriptDialog,
} from './components/dialogs/StudioDialogs';
import { codeTrust } from './harness/migrations';
import { PROFILES, SETTINGS } from './harness/profiles';
import { DIFF, HOSTILE_REVIEW, REVIEW } from './harness/trust';
import i18n from './i18n';
import { buildCommands } from './keyboard/commands';
import { CommandPalette } from './keyboard/CommandPalette';
import { ShortcutHelp } from './keyboard/ShortcutHelp';
import type { SessionState } from './session';
import { DeleteProfileDialog, PasswordDialog } from './views/connections/ConnectionDialogs';
import { ConnectionWizard } from './views/connections/ConnectionWizard';
import type { Workspace } from './workspace';

const health: typeof fetch = async () =>
  new Response(JSON.stringify({ status: 'ok', version: '0.1.0.dev0' }), { status: 200 });

const PROD: Workspace = {
  connection: { name: 'measure-central', environment: 'prod', status: 'connected', latencyMs: 12 },
  tabs: [
    { id: 'q1', title: 'Query 1', connection: 'measure-central', environment: 'prod' },
    { id: 'q2', title: 'parts-dev', connection: 'parts-dev', environment: 'dev' },
  ],
  activeTab: 'q1',
};

const noop = () => undefined;

/** STU-008: the connection manager with profiles of both groups, and the server settings. */
const profiles: typeof fetch = async (input) => {
  const url = String(input);
  if (url === '/api/profiles') return new Response(JSON.stringify(PROFILES), { status: 200 });
  if (url === '/api/settings') return new Response(JSON.stringify(SETTINGS), { status: 200 });
  if (url === '/api/secrets') {
    return new Response(JSON.stringify({ keyring: { available: false } }), { status: 200 });
  }
  return health(input);
};

/** STU-015: the project's Alembic files, not trusted yet. */
const migrations: typeof fetch = async (input) => {
  if (String(input) === '/api/project/code-trust') {
    return new Response(JSON.stringify(codeTrust(false)), { status: 200 });
  }
  return profiles(input);
};

const PAGES: [string, string, () => ReactElement][] = [
  [
    'command palette (STU-052)',
    '/',
    () => (
      <CommandPalette
        open
        commands={buildCommands({
          t: i18n.t.bind(i18n),
          path: '/',
          theme: 'system',
          density: 'compact',
          openHelp: noop,
        })}
        onClose={noop}
      />
    ),
  ],
  ['shortcut help (STU-052)', '/', () => <ShortcutHelp open onClose={noop} />],
  ['start page', '/', () => <App fetcher={health} heartbeat={false} />],
  ['connections', '/connections', () => <App fetcher={health} heartbeat={false} />],
  ['settings', '/settings', () => <App fetcher={health} heartbeat={false} />],
  ['not found', '/nowhere', () => <App fetcher={health} heartbeat={false} />],
  ['PROD workspace', '/', () => <App fetcher={health} heartbeat={false} workspace={PROD} />],
  ...(['used', 'expired', 'invalid', 'none', 'offline'] as SessionState[]).map(
    (session): [string, string, () => ReactElement] => [
      `sign-in notice (${session})`,
      '/',
      () => <App fetcher={health} heartbeat={false} session={session} />,
    ],
  ),
  [
    'D-01',
    '/',
    () => (
      <TrustProjectDialog
        open
        file=".tabrion/studio.toml"
        review={REVIEW}
        onTrust={noop}
        onReject={noop}
      />
    ),
  ],
  [
    'D-01 hostile and changed (review U-1)',
    '/',
    () => (
      <TrustProjectDialog
        open
        file=".tabrion/studio.toml"
        review={HOSTILE_REVIEW}
        diff={DIFF}
        onTrust={noop}
        onReject={noop}
      />
    ),
  ],
  [
    'D-02',
    '/',
    () => (
      <ProdConnectDialog
        open
        name="measure-central"
        timeoutSeconds={15}
        onConnect={noop}
        onCancel={noop}
      />
    ),
  ],
  [
    'D-06',
    '/',
    () => (
      <UnsavedScriptDialog open name="Query 2" onSave={noop} onDiscard={noop} onCancel={noop} />
    ),
  ],
  ['D-08', '/', () => <QuitDialog open running={2} onQuit={noop} onCancel={noop} />],
  ['migrations (STU-015)', '/migrations', () => <App fetcher={migrations} heartbeat={false} />],
  ['explorer (STU-010)', '/explorer', () => <App fetcher={profiles} heartbeat={false} />],
  ['diagnostics (STU-014)', '/diagnostics', () => <App fetcher={profiles} heartbeat={false} />],
  [
    'trust the migration code (STU-015)',
    '/',
    () => (
      <TrustCodeDialog
        open
        configFile="alembic.ini"
        scriptLocation="migrations"
        revisionFiles={['0001_parts.py']}
        stale
        onTrust={noop}
        onReject={noop}
      />
    ),
  ],
  [
    'connections with profiles (STU-008)',
    '/connections',
    () => <App fetcher={profiles} heartbeat={false} />,
  ],
  ['server settings (STU-008)', '/settings', () => <App fetcher={profiles} heartbeat={false} />],
  [
    'new connection assistant (STU-008)',
    '/',
    () => (
      <ConnectionWizard
        fetcher={profiles}
        item={null}
        digest={null}
        handWritten
        keyringAvailable={false}
        onSaved={noop}
        onStale={noop}
        onCancel={noop}
      />
    ),
  ],
  [
    'delete profile (STU-008)',
    '/',
    () => (
      <DeleteProfileDialog
        name="measure"
        problem={null}
        handWritten={false}
        onDelete={noop}
        onCancel={noop}
      />
    ),
  ],
  [
    'password (STU-008)',
    '/',
    () => (
      <PasswordDialog
        name="measure"
        keyringAvailable={false}
        keyringAllowed
        problem={null}
        onSave={noop}
        onCancel={noop}
      />
    ),
  ],
];

// jsdom has no canvas; axe probes it for colour checks, which are off here anyway.
beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
});

async function seriousViolations(): Promise<string[]> {
  const result = await axe.run(document.body, {
    resultTypes: ['violations'],
    // No layout and no canvas in jsdom: colour contrast is checked in Chromium (e2e/a11y.spec.ts).
    rules: { 'color-contrast': { enabled: false } },
  });
  return result.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
}

describe.each(['en', 'de'])('axe in %s', (language) => {
  beforeAll(async () => {
    await act(async () => {
      await i18n.changeLanguage(language);
    });
  });
  afterAll(async () => {
    window.history.replaceState(null, '', '/');
    await act(async () => {
      await i18n.changeLanguage('en');
    });
  });
  afterEach(cleanup);

  it.each(PAGES)('%s has no serious or critical violation', async (_name, path, page) => {
    window.history.replaceState(null, '', path);
    render(page());
    await act(async () => {
      // Two turns: the views fetch their data before they render it.
      await new Promise((resolve) => setTimeout(resolve, 0));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(await seriousViolations()).toEqual([]);
  });
});

it('the check itself finds critical violations (probe)', async () => {
  const main = document.createElement('main');
  main.append(
    document.createElement('h1'),
    document.createElement('img'),
    document.createElement('button'),
  );
  main.querySelector('h1')?.append('Probe');
  document.body.append(main);
  try {
    expect((await seriousViolations()).map((v) => v.split(' ')[0])).toEqual([
      'button-name',
      'image-alt',
    ]);
  } finally {
    main.remove();
  }
});
