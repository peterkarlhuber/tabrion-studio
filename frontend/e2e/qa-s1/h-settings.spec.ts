/**
 * QA-S1 section H, "Einstellungen": switching the language to English and back without a restart, with no English
 * sentence and no raw key on the German pages; light and dark with readable contrast in the explorer and the editor.
 */
import { readFileSync } from 'node:fs';

import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';

import {
  expect,
  goTo,
  openEditor,
  openInExplorer,
  skipWithoutStudio,
  startSession,
  test,
} from './studio';

skipWithoutStudio();

type Catalog = { [key: string]: string | Catalog };

function flatten(tree: Catalog, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === 'string') out[path] = value;
    else Object.assign(out, flatten(value, path));
  }
  return out;
}

const catalog = (language: 'en' | 'de') =>
  flatten(
    JSON.parse(
      readFileSync(new URL(`../../src/i18n/locales/${language}.json`, import.meta.url), 'utf8'),
    ) as Catalog,
  );
const EN = catalog('en');
const DE = catalog('de');

/**
 * English catalog phrases that a German page must not show (as in ux-acceptance-1-0.spec.ts): texts that differ
 * between the catalogs, have no placeholder and are a phrase of at least 12 characters with a space.
 */
const ENGLISH_ONLY = Object.entries(EN)
  .filter(
    ([key, value]) =>
      value !== DE[key] &&
      !value.includes('{{') &&
      !value.includes('<') &&
      value.includes(' ') &&
      value.length >= 12,
  )
  .map(([key, value]) => ({ key, value }));
/** Raw keys: a dotted name under one of the catalog's top-level groups (`editor.title`). */
const KEY_SHAPE = new RegExp(
  `\\b(?:${[...new Set(Object.keys(EN).map((key) => key.split('.')[0]))].join('|')})(?:\\.[A-Za-z_]+)+\\b`,
  'g',
);

/** The texts a user meets on a German page, screen-reader text included; text marked as another language is left out. */
async function germanTexts(page: Page): Promise<string> {
  return page.evaluate(() => {
    const own = (element: Element | null) =>
      (element?.closest('[lang]')?.getAttribute('lang') ?? 'de').split('-')[0] === 'de';
    const parts: string[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
      const parent = node.parentElement;
      if (parent === null || parent.closest('script, style') !== null || !own(parent)) continue;
      parts.push(node.textContent ?? '');
    }
    for (const element of Array.from(
      document.body.querySelectorAll('[aria-label], [title], [placeholder], [alt]'),
    )) {
      if (!own(element)) continue;
      for (const name of ['aria-label', 'title', 'placeholder', 'alt']) {
        const value = element.getAttribute(name);
        if (value) parts.push(value);
      }
    }
    return parts.join('\n');
  });
}

async function expectGerman(page: Page, where: string): Promise<void> {
  await expect(page.locator('html')).toHaveAttribute('lang', 'de');
  const shown = await germanTexts(page);
  const keys = [...shown.matchAll(KEY_SHAPE)].map((m) => m[0]).filter((k) => k in EN);
  expect(keys, `${where}: raw keys`).toEqual([]);
  const english = ENGLISH_ONLY.filter(({ value }) => shown.includes(value)).map(
    ({ key, value }) => `${key}: ${value}`,
  );
  expect(english, `${where}: English sentences`).toEqual([]);
}

/** The serious and critical color-contrast violations of the page (axe). */
async function contrast(page: Page): Promise<string[]> {
  const result = await new AxeBuilder({ page }).withRules(['color-contrast']).analyze();
  expect(
    [...result.passes, ...result.violations].some((rule) => rule.id === 'color-contrast'),
  ).toBe(true);
  return result.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .flatMap((v) => v.nodes.map((n) => `${n.target.join(' ')}: ${n.failureSummary ?? ''}`));
}

test('S1-30 "Sprache" switches to English and back at once; the German pages show no English sentence or raw key', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  // A marker in the page's window: it survives only if the page is not loaded again.
  await page.evaluate(() => {
    (window as unknown as { qaS1Marker?: boolean }).qaS1Marker = true;
  });
  await goTo(page, 'Einstellungen');
  await expect(page.getByRole('heading', { level: 1, name: 'Einstellungen' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Allgemein', exact: true })).toBeVisible();
  await page.getByRole('radio', { name: 'English', exact: true }).check();
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await goTo(page, 'Explorer');
  await expect(page.getByRole('heading', { level: 1, name: 'Explorer' })).toBeVisible();
  await goTo(page, 'SQL editor');
  await expect(page.getByRole('link', { name: 'Connections', exact: true })).toBeVisible();
  await goTo(page, 'Settings');
  await page.getByRole('radio', { name: 'Deutsch', exact: true }).check();
  await expect(page.getByRole('heading', { level: 1, name: 'Einstellungen' })).toBeVisible();
  expect(
    await page.evaluate(() => (window as unknown as { qaS1Marker?: boolean }).qaS1Marker),
  ).toBe(true);
  for (const [link, heading] of [
    ['Start', 'Willkommen'],
    ['Verbindungen', 'Verbindungen'],
    ['Explorer', 'Explorer'],
    ['SQL-Editor', 'SQL-Editor'],
    ['Abfrageverlauf', 'Abfrageverlauf'],
    ['Migrationen', 'Migrationen'],
    ['Diagnose', 'Diagnose'],
    ['Einstellungen', 'Einstellungen'],
  ] as const) {
    await goTo(page, link);
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
    await expectGerman(page, link);
  }
  // With content: an open explorer and a result in the editor.
  await openInExplorer(page, 'Shop (DEV)');
  await page.getByRole('treeitem', { name: 'orders', exact: true }).click();
  await expectGerman(page, 'Explorer, orders');
  await openEditor(page, 'Shop (DEV)');
  await page.locator('.cm-content').click();
  await page.keyboard.press('Control+A');
  await page.keyboard.insertText('select * from customers limit 5');
  await page.getByRole('button', { name: 'Ausführen', exact: true }).click();
  await expect(page.getByTestId('result-status')).toHaveText(/^5 Zeilen/);
  await expectGerman(page, 'SQL-Editor, Ergebnis');
});

test('S1-31 "Darstellung" Hell and Dunkel: the explorer and the SQL editor keep a readable contrast', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  for (const [label, theme] of [
    ['Hell', 'light'],
    ['Dunkel', 'dark'],
  ] as const) {
    await goTo(page, 'Einstellungen');
    await page.getByRole('radio', { name: label, exact: true }).check();
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await openInExplorer(page, 'Shop (DEV)');
    await page.getByRole('treeitem', { name: 'orders', exact: true }).click();
    expect(await contrast(page), `Explorer, ${label}`).toEqual([]);
    await openEditor(page, 'Shop (DEV)');
    await page.locator('.cm-content').click();
    await page.keyboard.press('Control+A');
    await page.keyboard.insertText('select * from orders limit 5');
    await page.getByRole('button', { name: 'Ausführen', exact: true }).click();
    await expect(page.getByTestId('result-status')).toHaveText(/^5 Zeilen/);
    expect(await contrast(page), `SQL-Editor, ${label}`).toEqual([]);
  }
});
