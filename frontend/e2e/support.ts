import AxeBuilder from '@axe-core/playwright';
import { expect, type Page } from '@playwright/test';

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
