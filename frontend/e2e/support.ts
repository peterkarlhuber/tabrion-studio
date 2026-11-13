import AxeBuilder from '@axe-core/playwright';
import { expect, type ConsoleMessage, type Page } from '@playwright/test';

export type Theme = 'light' | 'dark';

export interface HarnessOptions {
  scenario?: string;
  path?: string;
  theme?: Theme;
  lang?: 'en' | 'de';
}

/** Open the e2e harness (src/harness/main.tsx) and wait until the shell or the notice is rendered. */
export async function openHarness(page: Page, options: HarnessOptions = {}): Promise<void> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(options)) {
    if (value !== undefined) params.set(key, value);
  }
  await page.emulateMedia({ colorScheme: options.theme ?? 'light', reducedMotion: 'reduce' });
  await page.goto(`/harness.html?${params.toString()}`);
  await expect(page.locator('html')).toHaveAttribute('data-theme', options.theme ?? 'light');
  await expect(page.locator('h1').first()).toBeVisible();
  if (options.scenario === undefined || !['ended', 'used'].includes(options.scenario)) {
    await expect(page.getByTestId('backend-status')).toContainText('0.1.0.dev0');
  }
}

/** WCAG 2.0-2.2 A and AA rules, colour contrast included; the serious and critical violations. */
export async function seriousViolations(page: Page): Promise<string[]> {
  const result = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'])
    .analyze();
  // The colour contrast rule must really have judged the page (it is skipped silently without a layout).
  expect(
    [...result.passes, ...result.violations].some((rule) => rule.id === 'color-contrast'),
  ).toBe(true);
  return result.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
}

/** Every request and WebSocket the page opens whose origin is not the page's own (data: and blob: are local). */
export function foreignRequests(page: Page, origin: () => string): string[] {
  const foreign: string[] = [];
  const check = (url: string) => {
    if (/^(data|blob|about):/.test(url)) return;
    let target: string;
    try {
      target = new URL(url).origin;
    } catch {
      foreign.push(url);
      return;
    }
    // The development server's hot-reload socket is ws:// on the same host and port.
    const own = new URL(origin());
    const same =
      target === own.origin ||
      target === `${own.protocol === 'https:' ? 'wss' : 'ws'}://${own.host}`;
    if (!same) foreign.push(url);
  };
  // Requests on the browser context (review L-1 of stu-pr12-14-review.md): that includes popups, new pages and
  // service workers, which a listener on the page would miss. WebSockets are reported per page.
  const context = page.context();
  context.on('request', (request) => check(request.url()));
  const watch = (target: Page) => target.on('websocket', (socket) => check(socket.url()));
  watch(page);
  context.on('page', watch);
  return foreign;
}

/**
 * WebKit's console report for a refused inline style (TD-047). WebKit logs it once for every `<select>` a page under
 * `style-src 'self'` creates, with a `securitypolicyviolation` event for a `style-src-attr` of the select itself,
 * although the select has no `style` attribute: WebKit's own styling of the control, not the page's. WebKit words a
 * real refused `<style>` or `style` attribute the same way, so the text alone tells nothing; the event does.
 * `e2e/csp.spec.ts` shows both on a page with nothing but Studio's CSP and a plain `<select>`.
 */
export const WEBKIT_SELECT_CSP_REPORT =
  "Refused to apply a stylesheet because its hash, its nonce, or 'unsafe-inline' does not appear in the style-src directive of the Content Security Policy.";

/** What the page logs (a warning) for each violation event that is WebKit's own for a `<select>`. */
export const WEBKIT_SELECT_EVENT = 'TD-047: WebKit refused its own inline style of a <select>';

/** Whether a console message has the text and form of WebKit's report: an error at line 0, column 0 of the page. */
export function isWebKitSelectReport(page: Page, message: ConsoleMessage): boolean {
  if (page.context().browser()?.browserType().name() !== 'webkit') return false;
  const { url, lineNumber, columnNumber } = message.location();
  let sameOrigin: boolean;
  try {
    sameOrigin = new URL(url).origin === new URL(page.url()).origin;
  } catch {
    sameOrigin = false;
  }
  return (
    message.type() === 'error' &&
    message.text() === WEBKIT_SELECT_CSP_REPORT &&
    sameOrigin &&
    lineNumber === 0 &&
    columnNumber === 0
  );
}

/**
 * Record the page's CSP reports and its errors; call it before the page loads. Chromium and WebKit begin a report
 * with "Refused to", Firefox with "Content-Security-Policy:" (hyphenated); `e2e/csp.spec.ts` shows that the watcher
 * hears a refused `<style>` and `style` attribute in each browser.
 *
 * The one report left out is WebKit's own for a `<select>`, and only when it is paired with its event: a
 * `style-src-attr` violation of a `<select>` that has no `style` attribute. A report and an event cancel each other in
 * whichever order they arrive; a report without such an event stays a violation. Chromium and Firefox have no
 * exception, and assert with expectNoViolations, which waits until the page's events have arrived.
 */
export async function recordViolations(page: Page): Promise<string[]> {
  const violations: string[] = [];
  const webkit = page.context().browser()?.browserType().name() === 'webkit';
  let reports = 0; // WebKit reports in `violations` without their event yet
  let events = 0; // WebKit select events without their report yet
  if (webkit) {
    await page.addInitScript((marker: string) => {
      // Once per document, however often the watcher is set up for the page.
      const own = window as unknown as { webkitSelectWatch?: boolean };
      if (own.webkitSelectWatch) return;
      own.webkitSelectWatch = true;
      document.addEventListener(
        'securitypolicyviolation',
        (event) => {
          const target = event.target;
          if (
            event.effectiveDirective === 'style-src-attr' &&
            event.blockedURI === 'inline' &&
            target instanceof HTMLSelectElement &&
            !target.hasAttribute('style')
          )
            console.warn(marker);
        },
        true,
      );
    }, WEBKIT_SELECT_EVENT);
  }
  page.on('console', (message) => {
    const text = message.text();
    if (webkit && text === WEBKIT_SELECT_EVENT) {
      const index = violations.lastIndexOf(WEBKIT_SELECT_CSP_REPORT);
      if (reports > 0 && index >= 0) {
        violations.splice(index, 1);
        reports -= 1;
      } else events += 1;
      return;
    }
    if (!/Content[- ]Security[- ]Policy|Refused to/i.test(text)) return;
    if (isWebKitSelectReport(page, message)) {
      if (events > 0) {
        events -= 1;
        return;
      }
      reports += 1;
    }
    violations.push(text);
  });
  page.on('pageerror', (error) => violations.push(String(error)));
  return violations;
}

/**
 * Wait until the console messages of everything the page did so far have arrived: a warning logged from a task
 * queued after the pending violation events (console messages arrive in order).
 */
export async function settleConsole(page: Page): Promise<void> {
  const marker = `console settled ${Date.now()} ${Math.random()}`;
  const settled = page.waitForEvent('console', (message) => message.text() === marker);
  await page.evaluate(
    (text) =>
      new Promise<void>((resolve) =>
        setTimeout(() =>
          requestAnimationFrame(() =>
            setTimeout(() => {
              console.warn(text);
              resolve();
            }),
          ),
        ),
      ),
    marker,
  );
  await settled;
}

/** No CSP report and no page error so far (recordViolations). */
export async function expectNoViolations(page: Page, violations: string[]): Promise<void> {
  await settleConsole(page);
  expect(violations).toEqual([]);
}
