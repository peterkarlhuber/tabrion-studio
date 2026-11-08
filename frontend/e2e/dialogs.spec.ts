/**
 * STU-051 acceptance: the focus rules of every dialog in a real browser (08 section 6). The dialog is modal (the page
 * behind it is inert), opens with the focus on the option that changes nothing, keeps Tab and Shift+Tab inside, and
 * Escape cancels and returns the focus to the opener.
 */
import { test, expect } from '@playwright/test';

import { openHarness } from './support';

const DIALOGS: [string, string, string, string | null][] = [
  // scenario, title, safe option, destructive option
  ['dialog-d01', 'Trust the project profiles?', "Don't load", null],
  ['dialog-d02', 'Production database', 'Cancel', null],
  ['dialog-d06', 'Script not saved', 'Cancel', 'Discard'],
  ['dialog-d08', '2 queries are running', 'Cancel', 'Quit'],
];

for (const [scenario, title, safe, destructive] of DIALOGS) {
  test.describe(scenario, () => {
    test('is modal, named and opens on the safe option', async ({ page }) => {
      await openHarness(page, { scenario });
      const dialog = page.getByRole('dialog', { name: title });
      await expect(dialog).toBeVisible();
      await expect(dialog).toHaveAttribute('aria-modal', 'true');
      expect(await dialog.evaluate((el) => el.matches(':modal'))).toBe(true);
      await expect(dialog.getByRole('button', { name: safe, exact: true })).toBeFocused();
      if (destructive !== null) {
        await expect(
          dialog.getByRole('button', { name: destructive, exact: true }),
        ).not.toBeFocused();
      }
      const description = await dialog.evaluate(
        (el) =>
          document.getElementById(el.getAttribute('aria-describedby') ?? '')?.textContent ?? '',
      );
      expect(description.length).toBeGreaterThan(10);
      // The page behind is inert: a pointer over the navigation hits the dialog's backdrop, not the link.
      const link = page.getByRole('navigation').getByRole('link').first();
      const hit = await link.evaluate((el) => {
        const box = el.getBoundingClientRect();
        const target = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
        return target === el || el.contains(target);
      });
      expect(hit).toBe(false);
    });

    test('keeps the focus inside on Tab and Shift+Tab', async ({ page }) => {
      await openHarness(page, { scenario });
      const dialog = page.getByRole('dialog', { name: title });
      await expect(dialog).toBeVisible();
      const count = await dialog.getByRole('button').count();
      for (const key of ['Tab', 'Shift+Tab']) {
        for (let i = 0; i < count * 2 + 1; i += 1) {
          await page.keyboard.press(key);
          expect(await dialog.evaluate((el) => el.contains(document.activeElement))).toBe(true);
        }
      }
    });

    test('Escape cancels and the focus returns to the opener', async ({ page }) => {
      await openHarness(page, { scenario });
      await expect(page.getByRole('dialog', { name: title })).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toHaveCount(0);
      await expect(page.getByTestId('choice')).toHaveText('cancel');
      const opener = page.getByTestId('opener');
      await opener.focus();
      await page.keyboard.press('Enter');
      await expect(page.getByRole('dialog', { name: title })).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toHaveCount(0);
      await expect(opener).toBeFocused();
    });
  });
}
