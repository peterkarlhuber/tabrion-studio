/**
 * QA-S1 section A, "Erster Start und Verbinden": the first start, the trust question D-01, the way to the first table
 * and the header after connecting.
 */
import { expect, goTo, settled, signIn, skipWithoutStudio, test, trustProfiles } from './studio';

skipWithoutStudio();

test('S1-01 the started Studio serves the page on a loopback address and keeps running', async ({
  page,
  studio,
}) => {
  // The kit starts Studio for the project; the browser opens the one-time link (here: the test opens it).
  const link = studio.link();
  expect(new URL(link).hostname).toMatch(/^127(\.\d{1,3}){3}$/);
  await signIn(page, link);
  await expect(page).toHaveURL(`${studio.origin}/`);
  await expect(page.getByRole('heading', { level: 1, name: 'Willkommen' })).toBeVisible();
  // The server is still there after the page loaded: the health route answers.
  expect((await page.request.get(`${studio.origin}/api/health`)).ok()).toBe(true);
});

test('S1-02 the first start asks "Projektprofilen vertrauen?" and lists both profiles and their files', async ({
  page,
  studio,
}) => {
  await signIn(page, studio.link());
  const dialog = page.getByRole('dialog', { name: 'Projektprofilen vertrauen?' });
  await expect(dialog).toBeVisible();
  for (const text of ['Shop (DEV)', 'Shop (PROD)', 'daten/shop.sqlite', 'daten/shop-prod.sqlite']) {
    await expect(dialog).toContainText(text);
  }
  await expect(dialog.getByRole('button', { name: 'Vertrauen', exact: true })).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Nicht laden', exact: true })).toBeVisible();
});

test('S1-03 after "Vertrauen", the customers table of "Shop (DEV)" is reached in under 60 seconds', async ({
  page,
  studio,
}) => {
  await signIn(page, studio.link());
  await trustProfiles(page);
  const started = Date.now();
  // The way the checklist names: "Verbindungen" → "Shop (DEV)" → "Verbinden", then "Explorer" → "Öffnen".
  await goTo(page, 'Verbindungen');
  await page.getByTestId('connection-item').filter({ hasText: 'Shop (DEV)' }).click();
  await page
    .getByTestId('connection-detail')
    .getByRole('button', { name: 'Verbinden', exact: true })
    .click();
  await expect(page.getByTestId('connection-switcher')).toContainText('Shop (DEV)');
  await goTo(page, 'Explorer');
  const customers = page.getByRole('treeitem', { name: 'customers', exact: true });
  // The explorer preselects the connected profile and still needs "Öffnen" (checklist v2 names that step).
  await expect(
    page.getByLabel('Verbindungsprofil', { exact: true }).locator('option:checked'),
  ).toHaveText(/^Shop \(DEV\)/);
  await page.getByRole('button', { name: 'Öffnen', exact: true }).click();
  await expect(page.getByTestId('explorer-workspace')).toBeVisible();
  await customers.click();
  await expect(page.getByTestId('explorer-detail')).toContainText('customers');
  await expect(page.getByTestId('explorer-detail')).toContainText('email');
  const seconds = (Date.now() - started) / 1000;
  test.info().annotations.push({ type: 'seconds to first table', description: String(seconds) });
  expect(seconds).toBeLessThan(60);
});

test('S1-04 the header shows "Shop (DEV)", DEV and "Schreibgeschützt"; a click on it opens nothing', async ({
  page,
  studio,
}) => {
  await signIn(page, studio.link());
  await trustProfiles(page);
  await goTo(page, 'Verbindungen');
  await page.getByTestId('connection-item').filter({ hasText: 'Shop (DEV)' }).click();
  await page
    .getByTestId('connection-detail')
    .getByRole('button', { name: 'Verbinden', exact: true })
    .click();
  const header = page.getByRole('banner');
  await expect(header.getByTestId('connection-switcher')).toContainText('Shop (DEV)');
  await expect(header.getByTestId('env-badge')).toHaveAttribute('data-env', 'dev');
  await expect(header.getByTestId('env-badge')).toContainText('DEV');
  const chip = header.getByTestId('access-mode');
  await expect(chip).toHaveText(/^Schreibgeschützt/);
  await chip.click();
  await settled(page);
  await expect(page.getByRole('dialog')).toHaveCount(0);
});
