/**
 * STU-053, accessibility audit of the shell (08 section 9, WCAG 2.2 AA): the checks that axe does not make.
 *
 * - 2.5.8 Target size (minimum): every pointer target is at least 24 x 24 CSS px. Exempt are links inside a sentence
 *   (the inline exception) and a radio button or checkbox inside its label, where the label is the target.
 * - 1.4.10 Reflow: at 320 CSS px wide (400 % zoom of 1280 px) the page does not scroll sideways.
 * - 1.4.12 Text spacing: with the spacing of the success criterion, no control cuts its text.
 * - 2.4.7 / 2.4.11 Focus visible and not obscured: every Tab stop shows the 2 px ring and is in view.
 * - 2.3.3 / 08 section 9: prefers-reduced-motion turns the transitions off.
 * - 1.3.1 / 2.4.1: one banner, one main, a named navigation and a named status bar on every view.
 *
 * axe itself (with colour contrast, light and dark, en and de) is a11y.spec.ts; the dialogs' focus rules are
 * dialogs.spec.ts. The audit report is docs/architecture/accessibility.md.
 */
import { test, expect, type Page } from '@playwright/test';

import { openHarness, type HarnessOptions } from './support';

const STATES: [string, HarnessOptions, ((page: Page) => Promise<void>)?][] = [
  ['start page', {}],
  ['connections', { scenario: 'connections', path: '/connections' }],
  [
    'new connection assistant',
    { scenario: 'connections', path: '/connections' },
    async (page) => {
      await page.getByRole('button', { name: 'New connection' }).first().click();
      await expect(page.getByRole('dialog')).toBeVisible();
    },
  ],
  ['settings', { scenario: 'connections', path: '/settings' }],
  ['not found', { path: '/nowhere' }],
  ['PROD workspace', { scenario: 'prod' }],
  ['D-01 hostile and changed', { scenario: 'dialog-d01-hostile' }],
  ['D-02', { scenario: 'dialog-d02' }],
  ['D-06', { scenario: 'dialog-d06' }],
  ['D-08', { scenario: 'dialog-d08' }],
  ['session ended', { scenario: 'ended' }],
];

async function open(page: Page, options: HarnessOptions, then?: (page: Page) => Promise<void>) {
  await openHarness(page, options);
  await page.waitForLoadState('networkidle');
  if (then) await then(page);
}

/** Pointer targets smaller than 24 x 24 px that no exception covers. */
async function smallTargets(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    const selector =
      'a[href], button, input:not([type="hidden"]), select, textarea, summary, [role="tab"], [role="option"]';
    for (const element of Array.from(document.querySelectorAll<HTMLElement>(selector))) {
      if (element.closest('[inert]')) continue;
      let box = element.getBoundingClientRect();
      if (box.width === 0 && box.height === 0) continue; // not rendered
      if (element.classList.contains('tb-skip-link')) continue; // off screen until focused, then large
      const style = getComputedStyle(element);
      if (element.tagName === 'A' && style.display === 'inline') continue; // a link in a sentence
      const label = element.closest('label');
      if (label !== null && (element as HTMLInputElement).type?.match(/radio|checkbox/)) {
        box = label.getBoundingClientRect(); // the label is the target
      }
      if (box.width < 24 || box.height < 24) {
        const name = element.getAttribute('aria-label') ?? element.textContent ?? '';
        out.push(
          `${element.tagName} "${name.trim().slice(0, 40)}" ${Math.round(box.width)}x${Math.round(box.height)}`,
        );
      }
    }
    return out;
  });
}

for (const [name, options, then] of STATES) {
  test(`${name}: targets are at least 24 x 24 px (2.5.8)`, async ({ page }) => {
    await open(page, options, then);
    expect(await smallTargets(page)).toEqual([]);
  });

  test(`${name}: no sideways scrolling at 320 px (1.4.10)`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 640 });
    await open(page, options, then);
    const width = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(width.scroll).toBeLessThanOrEqual(width.client);
  });
}

test('the German texts reflow too (longer words)', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  for (const options of [{ scenario: 'prod' }, { scenario: 'connections', path: '/settings' }]) {
    await open(page, { ...options, lang: 'de' });
    const width = await page.evaluate(() => [
      document.documentElement.scrollWidth,
      document.documentElement.clientWidth,
    ]);
    expect(width[0]).toBeLessThanOrEqual(width[1] ?? 0);
  }
});

test('text spacing of 1.4.12 cuts no control text', async ({ page }) => {
  await open(page, { scenario: 'prod' });
  await page.addStyleTag({
    content: `* { line-height: 1.5 !important; letter-spacing: 0.12em !important;
      word-spacing: 0.16em !important; } p { margin-bottom: 2em !important; }`,
  });
  for (const path of ['Start', 'Connections', 'Settings']) {
    await page.getByRole('link', { name: path, exact: true }).click();
    await page.waitForLoadState('networkidle');
    const cut = await page.evaluate(() =>
      Array.from(document.querySelectorAll<HTMLElement>('button, a[href], label, h1, h2, legend'))
        .filter((el) => {
          const style = getComputedStyle(el);
          if (style.overflow === 'visible' && style.overflowX === 'visible') return false;
          if (style.textOverflow === 'ellipsis') return false; // names that may be long show "…" and a title
          return el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1;
        })
        .map((el) => `${el.tagName} "${(el.textContent ?? '').trim().slice(0, 30)}"`),
    );
    const page_ = await page.evaluate(() => [
      document.documentElement.scrollWidth,
      document.documentElement.clientWidth,
    ]);
    expect(cut, path).toEqual([]);
    expect(page_[0], path).toBeLessThanOrEqual(page_[1] ?? 0);
  }
});

test('every Tab stop of the shell shows the focus ring and is in view (2.4.7, 2.4.11)', async ({
  page,
}) => {
  await open(page, { scenario: 'prod' });
  const seen = new Set<string>();
  for (let step = 0; step < 15; step += 1) {
    await page.keyboard.press('Tab');
    const focus = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (el === null || el === document.body) return null;
      const style = getComputedStyle(el);
      const box = el.getBoundingClientRect();
      return {
        id: `${el.tagName}:${el.getAttribute('aria-label') ?? el.textContent?.trim().slice(0, 30)}`,
        ring: style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 2,
        inView:
          box.top >= 0 && box.left >= 0 && box.bottom <= innerHeight && box.right <= innerWidth,
      };
    });
    if (focus === null || seen.has(focus.id)) break;
    seen.add(focus.id);
    expect(focus.ring, focus.id).toBe(true);
    expect(focus.inView, focus.id).toBe(true);
  }
  expect(seen.size).toBeGreaterThanOrEqual(6); // skip link, brand, switcher, navigation, a tab
});

test('prefers-reduced-motion turns the transitions off (2.3.3)', async ({ page }) => {
  const duration = async () =>
    page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue('--tb-duration-fast').trim(),
    );
  await openHarness(page, { scenario: 'prod' }); // openHarness emulates reduce
  expect(await duration()).toBe('0ms');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  expect(await duration()).not.toBe('0ms');
});

test('every view has one banner, one main, a named navigation and status bar (1.3.1, 2.4.1)', async ({
  page,
}) => {
  for (const options of [
    {},
    { path: '/connections' },
    { path: '/settings' },
    { path: '/nowhere' },
  ]) {
    await open(page, options);
    await expect(page.getByRole('banner')).toHaveCount(1);
    await expect(page.getByRole('main')).toHaveCount(1);
    await expect(page.getByRole('navigation', { name: 'Main navigation' })).toHaveCount(1);
    await expect(page.getByRole('contentinfo', { name: 'Status bar' })).toHaveCount(1);
    await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
    await expect(page.getByRole('link', { name: 'Skip to content' })).toHaveAttribute(
      'href',
      '#main',
    );
  }
});

test('tooltips can be hovered and dismissed with Escape (1.4.13)', async ({ page }) => {
  await open(page, {});
  const link = page.getByRole('link', { name: 'Connections' });
  const tooltip = () =>
    link.evaluate((el) => {
      const style = getComputedStyle(el, '::after');
      return style.content === 'none' || style.display === 'none' ? null : style.content;
    });
  await link.hover();
  expect(await tooltip()).toBe('"Connections"');
  // Hoverable: the pointer moves from the link onto the tooltip without losing it.
  const box = await link.boundingBox();
  if (box === null) throw new Error('no link box');
  await page.mouse.move(box.x + box.width + 12, box.y + box.height / 2, { steps: 4 });
  expect(await tooltip()).toBe('"Connections"');
  // Dismissible: Escape hides it without moving the pointer or the focus.
  await page.keyboard.press('Escape');
  expect(await tooltip()).toBeNull();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  expect(await tooltip()).toBe('"Connections"');
});
