/**
 * STU-005 acceptance, contrast in the rendered page: the computed colours of the shell meet 4.5:1 for text and 3:1
 * for the focus ring and the PROD frame, in the light and the dark theme. The token pairs themselves are checked in
 * src/styles/tokens.test.ts on every CI run; this test checks that the page really uses them in a browser.
 */
import { test, expect, type Page } from '@playwright/test';

import { openHarness, type Theme } from './support';

function luminance([r, g, b]: number[]): number {
  const [lr = 0, lg = 0, lb = 0] = [r, g, b].map((v) => {
    const c = (v ?? 0) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * lr + 0.7152 * lg + 0.0722 * lb;
}

function ratio(a: number[], b: number[]): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

/** The colour of a property and the first opaque background behind the element, as [r, g, b]. */
async function colours(
  page: Page,
  selector: string,
  property: string,
): Promise<[number[], number[]]> {
  return page
    .locator(selector)
    .first()
    .evaluate((element, prop) => {
      const rgb = (value: string) => (value.match(/[\d.]+/g) ?? []).slice(0, 4).map(Number);
      let node: Element | null = element;
      let background = [255, 255, 255];
      while (node) {
        const value = rgb(getComputedStyle(node).backgroundColor);
        if (value.length === 3 || (value[3] ?? 0) > 0.99) {
          background = value.slice(0, 3);
          break;
        }
        node = node.parentElement;
      }
      return [rgb(getComputedStyle(element).getPropertyValue(prop)).slice(0, 3), background];
    }, property);
}

const TEXT: [string, string][] = [
  ['body text', 'main p'],
  ['header product name', 'header a span'],
  ['status bar', 'footer [role="status"]'],
  ['header PROD badge', 'header [data-testid="env-badge"]'],
  ['tab PROD badge', '[role="tab"] [data-testid="env-badge"]'],
  ['tab title', '[role="tab"][aria-selected="true"]'],
  ['access mode chip', '[data-testid="access-mode"]'],
];

for (const theme of ['light', 'dark'] as Theme[]) {
  test.describe(theme, () => {
    for (const [name, selector] of TEXT) {
      test(`${name} has text contrast of at least 4.5:1`, async ({ page }) => {
        await openHarness(page, { scenario: 'prod', theme });
        const [fg, bg] = await colours(page, selector, 'color');
        expect(ratio(fg, bg), `${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
      });
    }

    test('the PROD frame and the focus ring have at least 3:1', async ({ page }) => {
      await openHarness(page, { scenario: 'prod', theme });
      const [frame, behind] = await colours(page, '[data-env="prod"]', 'border-top-color');
      expect(ratio(frame, behind)).toBeGreaterThanOrEqual(3);
      await page.keyboard.press('Tab');
      await page.keyboard.press('Tab');
      const [ring, surface] = await colours(page, ':focus-visible', 'outline-color');
      expect(ratio(ring, surface)).toBeGreaterThanOrEqual(3);
    });
  });
}
