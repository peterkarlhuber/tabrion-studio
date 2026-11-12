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
  [
    'explorer with a table open (STU-010)',
    { scenario: 'explorer', path: '/explorer' },
    async (page) => {
      await page.getByTestId('explorer-open').click();
      await page.getByRole('treeitem', { name: 'part', exact: true }).click();
      await expect(page.getByTestId('detail-column')).toHaveCount(8);
    },
  ],
  ['not found', { path: '/nowhere' }],
  [
    'command palette (STU-052)',
    {},
    async (page) => {
      await page.keyboard.press('Control+k');
      await expect(page.getByTestId('command-palette')).toBeVisible();
    },
  ],
  [
    'shortcut help (STU-052)',
    {},
    async (page) => {
      await page.keyboard.press('Control+/');
      await expect(page.getByTestId('shortcut-help')).toBeVisible();
    },
  ],
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

/** Texts cut with "…" whose full text no title shows (WCAG 1.4.10 "without loss of information"). */
function untitledEllipses(): string[] {
  return Array.from(document.querySelectorAll<HTMLElement>('body *'))
    .filter((el) => {
      if (getComputedStyle(el).textOverflow !== 'ellipsis') return false;
      if (el.scrollWidth <= el.clientWidth + 1) return false; // not cut
      const text = (el.textContent ?? '').trim();
      return !(el.closest<HTMLElement>('[title]')?.title ?? '').includes(text);
    })
    .map((el) => `${el.tagName} "${(el.textContent ?? '').trim().slice(0, 30)}"`);
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
          return el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1;
        })
        .map((el) => `${el.tagName} "${(el.textContent ?? '').trim().slice(0, 30)}"`),
    );
    // A name cut with "…" (connection, tab) must show in full in a title (1.4.10 without loss of information).
    const untitled = await page.evaluate(untitledEllipses);
    const page_ = await page.evaluate(() => [
      document.documentElement.scrollWidth,
      document.documentElement.clientWidth,
    ]);
    expect(cut, path).toEqual([]);
    expect(untitled, path).toEqual([]);
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

test('at 320 px the header wraps instead of squeezing its parts (1.4.10)', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await open(page, { scenario: 'prod' });
  const header = page.getByRole('banner');
  // Every part of the header keeps its content's width: nothing is squeezed, cut or overlaps.
  const squeezed = await header.evaluate((root) =>
    Array.from(root.querySelectorAll<HTMLElement>('*'))
      // Visually hidden texts (1 x 1 px) are not shown, so they cannot be squeezed.
      .filter((el) => el.getBoundingClientRect().width > 1)
      .filter((el) => el.scrollWidth > el.clientWidth + 1)
      .map((el) => `${el.tagName}.${el.className} ${el.scrollWidth}>${el.clientWidth}`),
  );
  expect(squeezed).toEqual([]);
  // The logo mark keeps its 24 px.
  const mark = await header.locator('img').first().boundingBox();
  expect(mark?.width).toBe(24);
  expect(mark?.height).toBe(24);
  // The short connection name of the harness is shown in full, with its title.
  const name = page.getByTestId('connection-switcher').locator('[title]');
  await expect(name).toHaveAttribute('title', 'measure-central');
  expect(await name.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  // The header wraps onto more rows rather than overflowing: the tools sit below the brand.
  // Only the mark is shown, and the link keeps its name (4.1.2).
  const brand = await header.getByRole('link', { name: 'Tabrion Studio' }).boundingBox();
  const tools = await page.getByTestId('open-help').boundingBox();
  if (brand === null || tools === null) throw new Error('no header boxes');
  expect(tools.y).toBeGreaterThanOrEqual(brand.y + brand.height);
});

test('a long connection name is cut with "…" and shown in full in its title (1.4.10)', async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await open(page, { scenario: 'prod' });
  const name = page.getByTestId('connection-switcher').locator('[title]');
  const long = 'measure-central-replica-with-a-very-long-name-for-the-audit';
  // The harness name is short; the text and the title are lengthened together, as React renders them.
  await name.evaluate((el, text) => {
    el.textContent = text;
    el.setAttribute('title', text);
  }, long);
  expect(await name.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);
  expect(await page.evaluate(untitledEllipses)).toEqual([]);
  const width = await page.evaluate(() => [
    document.documentElement.scrollWidth,
    document.documentElement.clientWidth,
  ]);
  expect(width[0]).toBeLessThanOrEqual(width[1] ?? 0);
  await name.evaluate((el) => el.removeAttribute('title'));
  expect(await page.evaluate(untitledEllipses)).toEqual([
    expect.stringContaining('measure-central'),
  ]);
});

/** The ::after tooltip text of an element, or null when none is shown. */
function tooltipOf(element: Element): string | null {
  const style = getComputedStyle(element, '::after');
  return style.content === 'none' || style.display === 'none' ? null : style.content;
}

test('the header icon-button tooltip can be hovered and dismissed with Escape (1.4.13)', async ({
  page,
}) => {
  await open(page, {});
  const button = page.getByTestId('open-palette');
  const label = `"${(await button.getAttribute('aria-label')) ?? ''}"`;
  await button.hover();
  expect(await button.evaluate(tooltipOf)).toBe(label);
  // Hoverable, no gap: the pointer moves down through the 6 px spacing onto the tooltip without losing it.
  const box = await button.boundingBox();
  if (box === null) throw new Error('no button box');
  for (const dy of [2, 5, 12]) {
    await page.mouse.move(box.x + box.width - 4, box.y + box.height + dy, { steps: 2 });
    expect(await button.evaluate(tooltipOf), `${dy} px below`).toBe(label);
  }
  // Dismissible: Escape hides it without moving the pointer or the focus.
  await page.keyboard.press('Escape');
  expect(await button.evaluate(tooltipOf)).toBeNull();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  expect(await button.evaluate(tooltipOf)).toBe(label);
});

test('a click beside a focused link or button does not activate it through its tooltip', async ({
  page,
}) => {
  await open(page, {});
  const link = page.getByRole('link', { name: 'Connections' });
  // Keyboard focus shows the tooltip (:focus-visible) over the main area.
  await page.keyboard.press('Shift');
  await link.focus();
  expect(await link.evaluate(tooltipOf)).toBe('"Connections"');
  const box = await link.boundingBox();
  if (box === null) throw new Error('no link box');
  await page.mouse.click(box.x + box.width + 30, box.y + box.height / 2);
  await page.waitForTimeout(100);
  expect(new URL(page.url()).searchParams.get('path') ?? '/').not.toBe('/connections');
  await expect(page.getByRole('heading', { level: 1 })).not.toHaveText('Connections');

  const button = page.getByTestId('open-palette');
  await page.keyboard.press('Shift');
  await button.focus();
  expect(await button.evaluate(tooltipOf)).not.toBeNull();
  const buttonBox = await button.boundingBox();
  if (buttonBox === null) throw new Error('no button box');
  await page.mouse.click(buttonBox.x + buttonBox.width - 4, buttonBox.y + buttonBox.height + 12);
  await page.waitForTimeout(100);
  await expect(page.getByTestId('command-palette')).toBeHidden();
});
