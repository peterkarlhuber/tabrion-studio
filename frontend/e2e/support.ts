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
 * The console report WebKit logs once for every `<select>` a page under `style-src 'self'` creates (TD-047). It is
 * the browser's own: `e2e/csp.spec.ts` shows it on a page with nothing but a plain `<select>` under Studio's CSP, and
 * no report for the other form controls or in Chromium and Firefox. Studio's markup has no inline style for it to
 * refuse (its styles are CSS files and constructed style sheets).
 */
export const WEBKIT_SELECT_CSP_REPORT =
  "Refused to apply a stylesheet because its hash, its nonce, or 'unsafe-inline' does not appear in the style-src directive of the Content Security Policy.";

/**
 * Whether a console message is WebKit's report for a `<select>` and nothing else: only in WebKit, an error with
 * exactly that text, reported for the document itself at line 0, column 0 (a refused `<style>` or `style` attribute
 * in Studio's HTML would name its line). The same flows run in Chromium and Firefox with no exception, so a real
 * inline style of Studio still fails there.
 */
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
 * Record the page's CSP reports and its errors. Chromium and WebKit begin a report with "Refused to", Firefox with
 * "Content-Security-Policy:" (hyphenated); `e2e/csp.spec.ts` shows that each browser's report is heard. The only
 * report left out is WebKit's own for a `<select>` (isWebKitSelectReport).
 */
export function recordViolations(page: Page): string[] {
  const violations: string[] = [];
  page.on('console', (message) => {
    const text = message.text();
    if (!/Content[- ]Security[- ]Policy|Refused to/i.test(text)) return;
    if (isWebKitSelectReport(page, message)) return;
    violations.push(text);
  });
  page.on('pageerror', (error) => violations.push(String(error)));
  return violations;
}
