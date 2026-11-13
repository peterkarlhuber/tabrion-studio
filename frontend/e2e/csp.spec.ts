/**
 * TD-047: the CSP watcher of the real-server suites (`recordViolations`, support.ts) in every browser. A page with
 * nothing but Studio's Content-Security-Policy (the header of STU-003, read from `security/access.py`) and one element
 * at a time:
 *
 * - an inline `<style>`, a `style` attribute and a `<select>` with one, which the CSP refuses: the watcher must report
 *   each in each browser, on the console and as the page's violation events, so a run in Firefox or WebKit cannot
 *   pass because it never hears of a violation (each browser words the console message its own way);
 * - plain form controls: nothing to report, except that WebKit logs WEBKIT_SELECT_CSP_REPORT once for each `<select>`
 *   with a `style-src-attr` violation event for the select itself, which has no `style` attribute. That pair is the
 *   one report the watcher leaves out; WebKit words a refused `style` attribute of the page the same way, and that
 *   one the watcher still hears. When a WebKit update stops logging it, this test fails and the exception goes.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { test, expect, type ConsoleMessage } from '@playwright/test';

import {
  isWebKitSelectReport,
  pageViolationEvents,
  recordViolations,
  settleConsole,
  WEBKIT_SELECT_CSP_REPORT,
  WEBKIT_SELECT_EVENT,
} from './support';

/** CONTENT_SECURITY_POLICY of src/tabrion_studio/security/access.py, its string pieces joined. */
function studioCsp(): string {
  const source = readFileSync(
    join(import.meta.dirname, '..', '..', 'src', 'tabrion_studio', 'security', 'access.py'),
    'utf-8',
  );
  const block = /^CONTENT_SECURITY_POLICY = \(\n([\s\S]*?)\n\)$/m.exec(source)?.[1];
  if (block === undefined) throw new Error('CONTENT_SECURITY_POLICY not found');
  const csp = [...block.matchAll(/"([^"]*)"/g)].map((match) => match[1]).join('');
  expect(csp).toContain("style-src 'self'");
  return csp;
}

type Case =
  | 'div'
  | 'input'
  | 'button'
  | 'textarea'
  | 'select'
  | 'select with style'
  | 'style element'
  | 'style attribute';

test("the CSP watcher hears every browser's reports, and leaves out only WebKit's own for a <select>", async ({
  page,
  browserName,
}) => {
  const csp = studioCsp();
  await page.route('**/csp-probe.html', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'text/html; charset=utf-8',
      headers: { 'content-security-policy': csp },
      body: '<!doctype html><html lang="en"><head><title>CSP probe</title></head><body></body></html>',
    }),
  );
  const messages: ConsoleMessage[] = [];
  page.on('console', (message) => messages.push(message));
  const violations = await recordViolations(page);
  await page.goto('/csp-probe.html');
  // The browser's own violation events, next to its console messages (what each browser reports, in the log).
  await page.evaluate(() => {
    document.addEventListener('securitypolicyviolation', (event) =>
      console.warn(
        `probe event: ${event.effectiveDirective} ${event.blockedURI} ${(event.target as Element).nodeName}`,
      ),
    );
  });

  const cases: [string, Case][] = [
    ['div', 'div'],
    ['input', 'input'],
    ['button', 'button'],
    ['textarea', 'textarea'],
    ['select 1', 'select'],
    ['select 2', 'select'],
    ['select with style', 'select with style'],
    ['style element', 'style element'],
    ['style attribute', 'style attribute'],
  ];
  const observed: Record<string, { console: string[]; watcher: string[]; events: string[] }> = {};
  const reports: ConsoleMessage[] = [];
  for (const [key, kind] of cases) {
    const marker = `probe done: ${key}`;
    const before = messages.length;
    const heard = violations.length;
    const counted = (await pageViolationEvents(page))?.length ?? 0;
    // The marker is logged after the element is laid out and painted; console messages arrive in order, so every
    // report the element caused is in `messages` once the marker is.
    const done = page.waitForEvent('console', (message) => message.text() === marker);
    await page.evaluate(
      ({ kind, marker }) => {
        let element: HTMLElement;
        if (kind === 'style element') {
          element = document.createElement('style');
          element.textContent = 'body { color: rgb(1, 2, 3); }';
        } else if (kind === 'style attribute') {
          element = document.createElement('div');
          element.setAttribute('style', 'color: rgb(1, 2, 3);');
          element.textContent = 'one';
        } else if (kind === 'select with style') {
          element = document.createElement('select');
          element.setAttribute('style', 'color: rgb(1, 2, 3);');
          element.append(new Option('one'));
        } else {
          element = document.createElement(kind);
          if (element instanceof HTMLSelectElement)
            element.append(new Option('one'), new Option('two'));
          else element.textContent = 'one';
        }
        document.body.append(element);
        element.getBoundingClientRect();
        requestAnimationFrame(() => requestAnimationFrame(() => console.warn(marker)));
      },
      { kind, marker },
    );
    await done;
    // The violation events arrive after their reports; the watcher pairs WebKit's.
    await settleConsole(page);
    const own = messages
      .slice(before)
      .filter(
        (message) => message.text() !== marker && !message.text().startsWith('console settled'),
      );
    observed[key] = {
      console: own.map((message) => `${message.type()}: ${message.text()}`).sort(),
      watcher: violations.slice(heard),
      events: ((await pageViolationEvents(page)) ?? []).slice(counted),
    };
    if (kind === 'select')
      reports.push(...own.filter((message) => message.text() === WEBKIT_SELECT_CSP_REPORT));
  }
  // WebKit: the report, its event for the select itself, and the watcher's mark of that event (sorted).
  const select =
    browserName === 'webkit'
      ? [
          `error: ${WEBKIT_SELECT_CSP_REPORT}`,
          `warning: ${WEBKIT_SELECT_EVENT}`,
          'warning: probe event: style-src-attr inline SELECT',
        ]
      : [];
  const nothing = { console: [], watcher: [], events: [] };
  const refused = (event: RegExp) => ({
    console: expect.arrayContaining([]),
    watcher: [expect.any(String)],
    events: [expect.stringMatching(event)],
  });
  expect(observed).toEqual({
    div: nothing,
    input: nothing,
    button: nothing,
    textarea: nothing,
    'select 1': { console: select, watcher: [], events: [] },
    'select 2': { console: select, watcher: [], events: [] },
    // A select with a refused style attribute of the page's own is no exception, in WebKit either.
    'select with style': {
      console: expect.arrayContaining([]),
      watcher: expect.arrayContaining([expect.any(String)]),
      events: expect.arrayContaining([expect.stringMatching(/^style-src(-attr)? inline <SELECT>/)]),
    },
    'style element': refused(/^style-src(-elem)? inline <STYLE>/),
    'style attribute': refused(/^style-src(-attr)? inline <DIV>/),
  });
  // Each of WebKit's reports has the form the watcher pairs (an error at line 0, column 0 of the page).
  expect(reports).toHaveLength(browserName === 'webkit' ? 2 : 0);
  for (const message of reports) expect(isWebKitSelectReport(page, message)).toBe(true);
});

/**
 * TD-052 (C-1 of the STU-023 review): the ER diagram's choice of tables was a `<details>`, and WebKit reported a refused
 * style sheet when it appeared, with no `<select>` event to pair (CI run 43373752083). This probe settles the cause in
 * each browser: a bare `<details>` under Studio's CSP, and the disclosure that replaced it (a button with
 * `aria-expanded` and the region it shows). The events are printed, so a WebKit run shows what it refused. The product
 * renders no `<details>` (ESLint), and the disclosure must stay silent everywhere.
 */
test('a <details> is refused in WebKit under the CSP; the disclosure that replaces it is not', async ({
  page,
  browserName,
}) => {
  const csp = studioCsp();
  await page.route('**/csp-probe.html', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'text/html; charset=utf-8',
      headers: { 'content-security-policy': csp },
      body: '<!doctype html><html lang="en"><head><title>CSP probe</title></head><body></body></html>',
    }),
  );
  const messages: ConsoleMessage[] = [];
  page.on('console', (message) => messages.push(message));
  const violations = await recordViolations(page);
  await page.goto('/csp-probe.html');

  const observed: Record<'details' | 'disclosure', { console: string[]; events: string[] }> = {
    details: { console: [], events: [] },
    disclosure: { console: [], events: [] },
  };
  for (const kind of ['details', 'disclosure'] as const) {
    const marker = `probe done: ${kind}`;
    const before = messages.length;
    const counted = (await pageViolationEvents(page))?.length ?? 0;
    const done = page.waitForEvent('console', (message) => message.text() === marker);
    await page.evaluate(
      ({ kind, marker }) => {
        const box = document.createElement('div');
        if (kind === 'details') {
          const details = document.createElement('details');
          const summary = document.createElement('summary');
          summary.textContent = 'Tables: 2 of 6';
          const body = document.createElement('p');
          body.textContent = 'one';
          details.append(summary, body);
          box.append(details);
        } else {
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = 'Tables: 2 of 6';
          button.setAttribute('aria-expanded', 'false');
          button.setAttribute('aria-controls', 'probe-region');
          button.addEventListener('click', () => {
            const region = document.createElement('div');
            region.id = 'probe-region';
            region.textContent = 'one';
            box.append(region);
            button.setAttribute('aria-expanded', 'true');
          });
          box.append(button);
        }
        document.body.append(box);
        box.getBoundingClientRect();
        box.querySelector('button')?.click();
        requestAnimationFrame(() => requestAnimationFrame(() => console.warn(marker)));
      },
      { kind, marker },
    );
    await done;
    await settleConsole(page);
    observed[kind] = {
      console: messages
        .slice(before)
        .map((message) => message.text())
        .filter((text) => text !== marker && !text.startsWith('console settled')),
      events: ((await pageViolationEvents(page)) ?? []).slice(counted),
    };
  }
  console.warn(`TD-052 probe (${browserName}): ${JSON.stringify(observed)}`);
  // The replacement is silent in every browser.
  expect(observed.disclosure).toEqual({ console: [], events: [] });
  if (browserName === 'webkit') {
    // WebKit: the report STU-023's backend run saw, with a violation event that is not a <select>'s, so the TD-047
    // exemption does not and must not cover it (the event's target is printed above).
    expect(observed.details.console).toContain(WEBKIT_SELECT_CSP_REPORT);
    expect(observed.details.events.length).toBeGreaterThan(0);
    for (const event of observed.details.events) expect(event).not.toContain('<SELECT>');
  } else {
    expect(observed.details).toEqual({ console: [], events: [] });
  }
  expect(violations.length).toBe(browserName === 'webkit' ? observed.details.console.length : 0);
});
