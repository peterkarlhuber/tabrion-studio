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
  TrustProjectDialog,
  UnsavedScriptDialog,
} from './components/dialogs/StudioDialogs';
import { DIFF, HOSTILE_REVIEW, REVIEW } from './harness/trust';
import i18n from './i18n';
import type { SessionState } from './session';
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

const PAGES: [string, string, () => ReactElement][] = [
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
      await Promise.resolve();
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
