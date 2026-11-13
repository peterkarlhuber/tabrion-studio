/**
 * TD-047: the CSP watcher of the real-server suites (`recordViolations`, support.ts) in every browser. A page with
 * nothing but Studio's Content-Security-Policy (the header of STU-003, read from `security/access.py`) and one element
 * at a time:
 *
 * - an inline `<style>` and a `style` attribute, which the CSP refuses: the watcher must report both in each browser,
 *   so a run in Firefox or WebKit cannot pass because it never hears of a violation (each browser words the console
 *   message its own way);
 * - plain form controls: nothing to report, except that WebKit logs WEBKIT_SELECT_CSP_REPORT once for each `<select>`,
 *   the one report the watcher leaves out. When a WebKit update stops logging it, this test fails and the exception
 *   goes.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { test, expect, type ConsoleMessage } from '@playwright/test';

import { isWebKitSelectReport, recordViolations, WEBKIT_SELECT_CSP_REPORT } from './support';

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
  'div' | 'input' | 'button' | 'textarea' | 'select' | 'style element' | 'style attribute';

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
  const violations = recordViolations(page);
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
    ['style element', 'style element'],
    ['style attribute', 'style attribute'],
  ];
  const observed: Record<string, { console: string[]; watcher: string[] }> = {};
  const reports: ConsoleMessage[] = [];
  for (const [key, kind] of cases) {
    const marker = `probe done: ${key}`;
    const before = messages.length;
    const heard = violations.length;
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
    const own = messages
      .slice(before)
      .filter((message) => message.text() !== marker && !message.text().startsWith('probe event:'));
    observed[key] = {
      console: [
        ...messages
          .slice(before)
          .filter((message) => message.text() !== marker)
          .map((message) => `${message.type()}: ${message.text()}`),
      ],
      watcher: violations.slice(heard),
    };
    if (kind === 'select') reports.push(...own);
  }
  const select = browserName === 'webkit' ? [`error: ${WEBKIT_SELECT_CSP_REPORT}`] : [];
  const nothing = { console: [], watcher: [] };
  const refused = { console: expect.arrayContaining([]), watcher: [expect.any(String)] };
  expect(observed).toEqual({
    div: nothing,
    input: nothing,
    button: nothing,
    textarea: nothing,
    'select 1': { console: select, watcher: [] },
    'select 2': { console: select, watcher: [] },
    'style element': refused,
    'style attribute': refused,
  });
  // The watcher's exception matches each of WebKit's reports (an error at line 0, column 0 of the page).
  for (const message of reports) expect(isWebKitSelectReport(page, message)).toBe(true);
});
