/**
 * TD-062 (JR-N1, G-S1 decision 4): the PROD case of "Reconnect" after a failed reconnect, in Chromium against the e2e
 * harness, in light and dark, English and German, with axe. After a lost connection, the first "Reconnect" asks D-02.
 * When that attempt fails (the database is down: `window.harnessDatabaseDown(true)` of src/harness/main.tsx), the retry
 * in the same page session does not ask again: only a loss forgets the confirmation, a failed attempt does not
 * (docs/architecture/connections.md, "Lost connections and 'Reconnect'"). A new loss asks D-02 again.
 */
import { test, expect, type Page } from '@playwright/test';

import { openHarness, seriousViolations, type Theme } from './support';

async function lose(page: Page, connectionId: string): Promise<void> {
  await page.evaluate((id) => {
    (window as unknown as { harnessLose: (id: string) => void }).harnessLose(id);
  }, connectionId);
}

async function databaseDown(page: Page, down: boolean): Promise<void> {
  await page.evaluate((value) => {
    (window as unknown as { harnessDatabaseDown: (down: boolean) => void }).harnessDatabaseDown(
      value,
    );
  }, down);
}

/** The bodies of every `POST /api/connections` the page sent, failed ones included. */
async function opens(page: Page): Promise<unknown[]> {
  const bodies = await page.evaluate(
    () => (window as unknown as { connectionOpens: string[] }).connectionOpens,
  );
  return bodies.map((body) => JSON.parse(body) as unknown);
}

const PROD_OPEN = { profile_id: 'user:measure-central', confirmed_environment: 'prod' };

const TEXT = {
  en: {
    title: 'Connection lost: measure-central',
    reconnect: 'Reconnect',
    failed: 'Reconnecting failed: ',
    dialog: 'Production database',
    connect: 'Connect',
  },
  de: {
    title: 'Verbindung getrennt: measure-central',
    reconnect: 'Neu verbinden',
    failed: 'Neu verbinden fehlgeschlagen: ',
    dialog: 'Produktionsdatenbank',
    connect: 'Verbinden',
  },
} as const;

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as const) {
    test(`PROD: D-02 on the first Reconnect, none on the retry after a failed one, D-02 after a new loss (${theme}, ${lang})`, async ({
      page,
    }) => {
      const text = TEXT[lang];
      const production = page.getByRole('dialog', { name: text.dialog });
      await openHarness(page, { scenario: 'explorer', path: '/explorer', theme, lang });
      await page.getByRole('combobox').first().selectOption('user:measure-central');
      await page.getByTestId('explorer-open').click();
      await production.getByRole('button', { name: text.connect }).click();
      await expect(page.getByRole('tree')).toBeVisible();
      expect(await opens(page)).toEqual([PROD_OPEN]);

      // The connection is lost and the database is down.
      await lose(page, 'c-pg');
      await databaseDown(page, true);
      await page.getByTestId('explorer-refresh').click();
      const banner = page.getByTestId('connection-lost');
      await expect(banner).toContainText(text.title);
      await expect(banner.getByTestId('env-badge')).toHaveAttribute('data-env', 'prod');
      expect(await seriousViolations(page)).toEqual([]);

      // The first "Reconnect" asks D-02 (the loss forgot the confirmation); the attempt fails.
      await banner.getByRole('button', { name: text.reconnect }).click();
      await expect(production).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
      await production.getByRole('button', { name: text.connect }).click();
      await expect(production).toHaveCount(0);
      const problem = banner.getByTestId('connection-reconnect-problem');
      await expect(problem).toContainText(text.failed);
      await expect(banner).toBeVisible();
      expect(await opens(page)).toEqual([PROD_OPEN, PROD_OPEN]);
      expect(await seriousViolations(page)).toEqual([]);

      // The retry after the failed attempt does not ask again: it opens at once (confirmed) and fails again.
      await banner.getByRole('button', { name: text.reconnect }).click();
      await expect.poll(async () => (await opens(page)).length).toBe(3);
      await expect(problem).toContainText(text.failed);
      await expect(production).toHaveCount(0);
      expect(await opens(page)).toEqual([PROD_OPEN, PROD_OPEN, PROD_OPEN]);

      // The database is back: the next retry still does not ask, and the view follows the new connection.
      await databaseDown(page, false);
      await banner.getByRole('button', { name: text.reconnect }).click();
      await expect(page.getByTestId('connection-lost')).toHaveCount(0);
      await expect(production).toHaveCount(0);
      await expect(page.getByRole('main')).toBeFocused();
      await expect(page.getByRole('tree')).toBeVisible();
      expect(await opens(page)).toHaveLength(4);

      // A new loss forgets the confirmation again: D-02 on "Reconnect".
      await lose(page, 'c-pg');
      await page.getByTestId('explorer-refresh').click();
      await expect(banner).toContainText(text.title);
      await banner.getByRole('button', { name: text.reconnect }).click();
      await expect(production).toBeVisible();
      expect(await opens(page)).toHaveLength(4);
      await production.getByRole('button', { name: text.connect }).click();
      await expect(page.getByTestId('connection-lost')).toHaveCount(0);
      await expect(page.getByRole('tree')).toBeVisible();
      expect(await opens(page)).toEqual([PROD_OPEN, PROD_OPEN, PROD_OPEN, PROD_OPEN, PROD_OPEN]);
    });
  }
}
