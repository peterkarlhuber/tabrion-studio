/** STU-051 acceptance: the PROD badge in the header, on the tabs and in the window title, plus the red frame. */
import { test, expect } from '@playwright/test';

import { openHarness } from './support';

test('a PROD connection is marked in the header, on every tab and in the title', async ({
  page,
}) => {
  await openHarness(page, { scenario: 'prod' });
  await expect(page).toHaveTitle('[PROD] measure-central – Tabrion Studio');
  const header = page.getByRole('banner');
  await expect(header.getByTestId('env-badge')).toHaveText('Environment: PROD');
  await expect(header.getByTestId('env-badge')).toHaveAttribute('data-env', 'prod');
  await expect(header.getByTestId('access-mode')).toContainText('Writing locked');
  const tabs = page.getByRole('tab');
  await expect(tabs).toHaveCount(3);
  await expect(tabs.nth(0)).toHaveAccessibleName(/^Query 1\s*,\s*Environment:\s*PROD$/);
  await expect(tabs.nth(0).getByTestId('env-badge')).toHaveText('Environment: PROD');
  await expect(tabs.nth(1).getByTestId('env-badge')).toHaveText('Environment: DEV');
  await expect(tabs.nth(2).getByTestId('env-badge')).toHaveText('Environment: TEST');
  // The 3 px red frame at the top (08 section 3).
  const frame = await page
    .locator('[data-env="prod"]')
    .first()
    .evaluate(
      (el) => getComputedStyle(el).borderTopWidth + ' ' + getComputedStyle(el).borderTopStyle,
    );
  expect(frame).toBe('3px solid');
  // The badge stays visible with its text (colour is not the only carrier).
  const badge = header.getByTestId('env-badge');
  await expect(badge).toBeVisible();
  expect(await badge.evaluate((el) => el.lastChild?.textContent)).toBe('PROD');
});

test('the PROD title and badges are the same in German', async ({ page }) => {
  await openHarness(page, { scenario: 'prod', lang: 'de' });
  await expect(page).toHaveTitle('[PROD] measure-central – Tabrion Studio');
  await expect(page.getByRole('banner').getByTestId('env-badge')).toHaveText('Umgebung: PROD');
  await expect(page.getByRole('tab').first()).toHaveAccessibleName(
    /^Query 1\s*,\s*Umgebung:\s*PROD$/,
  );
  await expect(page.locator('html')).toHaveAttribute('lang', 'de');
});

test('without a connection there is no environment badge and no frame', async ({ page }) => {
  await openHarness(page);
  await expect(page).toHaveTitle('Tabrion Studio');
  await expect(page.getByTestId('env-badge')).toHaveCount(0);
  await expect(page.locator('[data-env="prod"]')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'No connection', exact: true })).toBeVisible();
});

test('S-1: an ended session says so and stops the heartbeat', async ({ page }) => {
  const beats = () => page.evaluate(() => (window as unknown as { heartbeats: number }).heartbeats);
  await page.clock.install();
  await openHarness(page, { scenario: 'ended' });
  await expect(page.getByRole('alert')).toHaveText(
    'Your session has ended. Run tabrion-studio again to open Studio.',
  );
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Session ended');
  await expect(page.getByRole('navigation')).toHaveCount(0);
  const sent = await beats();
  expect(sent).toBeGreaterThan(0);
  await page.clock.fastForward('05:00');
  expect(await beats()).toBe(sent);
});

test('the theme follows the system and the logo follows the theme', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/harness.html');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.getByRole('img', { name: 'Tabrion Studio' })).toHaveAttribute(
    'src',
    '/branding/tabrion-studio-logo-on-dark.png',
  );
  await page.emulateMedia({ colorScheme: 'light' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.getByRole('img', { name: 'Tabrion Studio' })).toHaveAttribute(
    'src',
    '/branding/tabrion-studio-logo.png',
  );
  // No CSS filter on any logo image.
  const filters = await page
    .locator('img')
    .evaluateAll((imgs) => imgs.map((img) => getComputedStyle(img).filter));
  expect(filters.every((f) => f === 'none')).toBe(true);
});
