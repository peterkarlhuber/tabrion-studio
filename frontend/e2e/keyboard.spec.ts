/**
 * STU-052 (08 section 7): the command palette, the shortcut help and F6, keyboard only, in Chromium. The palette and
 * the help are modal (the page behind them is inert), return the focus, and pass axe with colour contrast in light
 * and dark, English and German.
 */
import { test, expect, type Page } from '@playwright/test';

import { openHarness, seriousViolations, type Theme } from './support';

async function openPalette(page: Page) {
  await page.keyboard.press('Control+k');
  const palette = page.getByTestId('command-palette');
  await expect(palette).toBeVisible();
  return palette;
}

test('keyboard only: palette to Settings, F6 through the areas, help and back', async ({
  page,
}) => {
  await openHarness(page, { scenario: 'prod' });
  const palette = await openPalette(page);
  const search = palette.getByRole('combobox');
  await expect(search).toBeFocused();
  await page.keyboard.type('settings');
  await expect(palette.getByRole('option')).toHaveCount(1);
  await page.keyboard.press('Enter');
  await expect(palette).toBeHidden();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
  // The focus is on the new page's main area (STU-053, review I-2 of stu-052-td021-review.md).
  await expect(page.locator('#main')).toBeFocused();

  // F6: status bar, then around: header, navigation (the current page), tabs (the selected one), main area.
  await page.keyboard.press('F6');
  await expect(page.getByRole('contentinfo')).toBeFocused();
  await page.keyboard.press('F6');
  await expect(page.getByRole('link', { name: 'Tabrion Studio' })).toBeFocused();
  await page.keyboard.press('F6');
  await expect(page.getByRole('link', { name: 'Settings' })).toBeFocused();
  await page.keyboard.press('F6');
  await expect(page.getByRole('tab', { selected: true })).toBeFocused();
  await page.keyboard.press('F6');
  await expect(page.locator('#main')).toBeFocused();
  const ring = await page
    .locator('#main')
    .evaluate((el) => getComputedStyle(el).outlineStyle + ' ' + getComputedStyle(el).outlineWidth);
  expect(ring).toBe('solid 2px'); // a keyboard move shows the focus (WCAG 2.4.7)
  await page.keyboard.press('F6');
  await expect(page.getByRole('contentinfo')).toBeFocused();
  await page.keyboard.press('Shift+F6');
  await expect(page.locator('#main')).toBeFocused();

  // Help, then Escape: the focus is back where it was.
  await page.keyboard.press('Control+/');
  const help = page.getByTestId('shortcut-help');
  await expect(help).toBeVisible();
  await expect(help.locator('[data-action="close"]')).toBeFocused();
  await expect(help.getByRole('row')).toHaveCount(5);
  await page.keyboard.press('Escape');
  await expect(help).toBeHidden();
  await expect(page.locator('#main')).toBeFocused();
});

test('the palette is modal and Escape returns the focus to the opener', async ({ page }) => {
  await openHarness(page);
  const button = page.getByTestId('open-palette');
  await button.focus();
  await page.keyboard.press('Enter');
  const palette = page.getByTestId('command-palette');
  await expect(palette).toBeVisible();
  // The page behind a modal <dialog> is inert: a click there does not reach it.
  const inert = await page.evaluate(() => {
    const dialog = document.querySelector('dialog[open]');
    return dialog !== null && dialog.matches(':modal');
  });
  expect(inert).toBe(true);
  await page.keyboard.press('Tab');
  await expect(palette.getByRole('combobox')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(palette).toBeHidden();
  await expect(button).toBeFocused();
});

test('the theme command changes the theme at once', async ({ page }) => {
  await openHarness(page, { theme: 'light' });
  await openPalette(page);
  await page.keyboard.type('theme dark');
  await page.keyboard.press('Enter');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
});

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as const) {
    test(`${theme}, ${lang}: palette and help have no serious or critical axe violation`, async ({
      page,
    }) => {
      await openHarness(page, { theme, lang });
      await openPalette(page);
      expect(await seriousViolations(page)).toEqual([]);
      await page.keyboard.type('zzz'); // the empty state
      expect(await seriousViolations(page)).toEqual([]);
      await page.keyboard.press('Escape');
      await page.keyboard.press('Control+/');
      await expect(page.getByTestId('shortcut-help')).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
    });
  }
}
