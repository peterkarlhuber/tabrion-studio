/**
 * STU-023: the ER diagram of the explorer in the browser (harness). The acceptance of 09: 150 tables laid out in
 * under 2 s, measured in the page (the layout's own time, `data-layout-ms`, not the test's wall clock), and the SVG and
 * PNG exports, made in the page without a remote resource. Also: axe in light and dark, English and German; the
 * diagram's tables reached with the keyboard; the text alternative (08 section 4.7) lists the same tables and keys; no
 * write is sent. The CSP part runs against a real server in `backend.spec.ts`.
 */
import { readFileSync } from 'node:fs';

import { test, expect, type Page } from '@playwright/test';

import { openHarness, seriousViolations, type Theme } from './support';

async function writes(page: Page) {
  return page.evaluate(
    () =>
      (window as unknown as { writes: { method: string; url: string; contentType: string }[] })
        .writes,
  );
}

async function openDiagram(page: Page, profile?: string) {
  if (profile !== undefined) await page.getByRole('combobox').first().selectOption(profile);
  await page.getByTestId('explorer-open').click();
  await expect(page.getByTestId('explorer-workspace')).toBeVisible();
  await page.getByTestId('explorer-view-diagram').click();
  await expect(page.getByTestId('er-panel')).toBeVisible();
}

const TEXT = {
  en: { diagram: 'ER diagram', status: /^Tables: 6, relationships: 3, layout: \d+ ms\.$/ },
  de: { diagram: 'ER-Diagramm', status: /^Tabellen: 6, Beziehungen: 3, Layout: \d+ ms\.$/ },
};

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const lang of ['en', 'de'] as const) {
    test(`${theme}, ${lang}: the diagram, its choice and its table alternative pass axe`, async ({
      page,
    }) => {
      test.setTimeout(90_000);
      await openHarness(page, { scenario: 'explorer', path: '/explorer', theme, lang });
      await openDiagram(page);
      await expect(page.getByRole('heading', { name: TEXT[lang].diagram, level: 2 })).toBeVisible();
      // The parts app: six tables; part points at material, part_relation and property at part.
      await expect(page.getByTestId('er-status')).toHaveText(TEXT[lang].status);
      await expect(page.getByTestId('er-box')).toHaveCount(6);
      await expect(page.getByTestId('er-list-row')).toHaveCount(6);
      expect(await seriousViolations(page)).toEqual([]);
      await page.getByTestId('er-choose-toggle').click();
      await expect(page.getByTestId('er-choose-filter')).toBeVisible();
      expect(await seriousViolations(page)).toEqual([]);
      // PostgreSQL: the schema public, one key.
      await openDiagram(page, 'project:parts-pg');
      await expect(page.getByTestId('er-box')).toHaveCount(3);
      expect(await seriousViolations(page)).toEqual([]);
      expect(await writes(page)).toEqual([
        { method: 'POST', url: '/api/connections', contentType: 'application/json' },
        { method: 'POST', url: '/api/connections', contentType: 'application/json' },
      ]);
    });
  }
}

test('09 acceptance: 150 tables are laid out in under 2 s, measured in the page', async ({
  page,
}) => {
  await openHarness(page, { scenario: 'explorer', path: '/explorer', er: '150' });
  await openDiagram(page, 'project:parts-pg');
  await page.getByTestId('er-schema').selectOption('audit');
  const status = page.getByTestId('er-status');
  await expect(status).toHaveText(/^Tables: 150, relationships: 170, layout: \d+ ms\.$/);
  const ms = Number(await status.getAttribute('data-layout-ms'));
  expect(Number.isFinite(ms) && ms > 0).toBe(true);
  expect(ms).toBeLessThan(2000);
  await expect(page.getByTestId('er-box')).toHaveCount(150);
  await expect(page.getByTestId('er-list-row')).toHaveCount(150);
  // The key into public is listed in the table alternative, not drawn.
  await expect(
    page.getByTestId('er-list-row').filter({ hasText: 'audit_001_device_fkey' }),
  ).toContainText('public.device (id) (not in the diagram)');
  // "Lay out again" measures again.
  await page.getByTestId('er-relayout').click();
  await expect(status).toHaveText(/^Tables: 150, relationships: 170, layout: \d+ ms\.$/);
  expect(Number(await status.getAttribute('data-layout-ms'))).toBeLessThan(2000);
});

test('SVG and PNG are saved from the page, with the shown tables and nothing remote', async ({
  page,
}) => {
  await openHarness(page, { scenario: 'explorer', path: '/explorer', er: '150' });
  const requests: string[] = [];
  page.on('request', (request) => requests.push(request.url()));
  await openDiagram(page, 'project:parts-pg');
  await page.getByTestId('er-schema').selectOption('audit');
  await expect(page.getByTestId('er-box')).toHaveCount(150);
  const before = requests.length;

  const svgDownload = page.waitForEvent('download');
  await page.getByTestId('er-svg').click();
  const svg = await svgDownload;
  expect(svg.suggestedFilename()).toBe('er-diagram-audit.svg');
  const text = readFileSync(await svg.path(), 'utf8');
  expect(
    text.startsWith(
      '<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg"',
    ),
  ).toBe(true);
  expect(text.match(/<g transform=/g)).toHaveLength(150);
  expect(text.match(/marker-end="url\(#er-arrow\)"/g)).toHaveLength(170);
  expect(text).toContain('>audit_149<');
  // No reference out of the file: the only URL is the SVG namespace, the only url() the arrow marker.
  expect(text.match(/https?:\/\//g)).toEqual(['http://']);
  expect(text).not.toMatch(/href|<script|<style|\sstyle=|<image|<foreignObject|@import/i);
  await expect(page.getByTestId('er-status')).toContainText('Saved er-diagram-audit.svg.');

  const pngDownload = page.waitForEvent('download');
  await page.getByTestId('er-png').click();
  const png = await pngDownload;
  expect(png.suggestedFilename()).toBe('er-diagram-audit.png');
  const bytes = readFileSync(await png.path());
  expect([...bytes.subarray(0, 8)]).toEqual([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  // IHDR: twice the SVG's size where the canvas holds it, else less (never more).
  const width = bytes.readUInt32BE(16);
  const height = bytes.readUInt32BE(20);
  const svgWidth = Number(/<svg [^>]*width="(\d+)"/.exec(text)?.[1]);
  const svgHeight = Number(/<svg [^>]*height="(\d+)"/.exec(text)?.[1]);
  expect(width).toBeLessThanOrEqual(svgWidth * 2);
  expect(width).toBeGreaterThanOrEqual(Math.min(svgWidth * 2, 1000));
  expect(width * height).toBeLessThanOrEqual(16_000_000);
  expect(Math.abs(width / height - svgWidth / svgHeight)).toBeLessThan(0.02);
  // Neither export asked anything of a server.
  expect(requests.slice(before)).toEqual([]);
  expect(await writes(page)).toHaveLength(1);
});

test('mouse (QA-S1 S1-09): a click on a box of the diagram opens its table in the explorer', async ({
  page,
}) => {
  await openHarness(page, { scenario: 'explorer', path: '/explorer' });
  for (const part of ['name', 'column'] as const) {
    await openDiagram(page);
    await expect(page.getByTestId('er-box')).toHaveCount(6);
    // A box whose name lies inside the visible canvas (the fitted view can put a box under the status bar).
    const canvas = await page.getByTestId('er-canvas').boundingBox();
    expect(canvas).not.toBeNull();
    let table = '';
    for (const box of await page.getByTestId('er-canvas').getByTestId('er-box').all()) {
      const rect = await box.getByRole('button').boundingBox();
      if (
        canvas !== null &&
        rect !== null &&
        rect.y >= canvas.y &&
        rect.y + rect.height <= canvas.y + canvas.height / 2
      ) {
        table = (await box.getAttribute('data-table')) ?? '';
        break;
      }
    }
    expect(table).not.toBe('');
    const box = page
      .getByTestId('er-canvas')
      .locator(`[data-testid="er-box"][data-table="${table}"]`);
    // A real mouse click where a user clicks: the table's name, or a column row of the box. Playwright clicks the
    // point on the screen and fails if another element (the canvas behind a box without pointer events) takes it.
    await (
      part === 'name'
        ? box.getByRole('button', { name: `Show ${table} in the explorer`, exact: true })
        : box.getByRole('listitem').first()
    ).click();
    await expect(page.getByTestId('explorer-view-objects')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('treeitem', { name: table, exact: true })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  }
});

test('keyboard: a table of the diagram opens it in the explorer; the choice changes the diagram', async ({
  page,
}) => {
  await openHarness(page, { scenario: 'explorer', path: '/explorer' });
  await openDiagram(page);
  await expect(page.getByTestId('er-box')).toHaveCount(6);
  // The boxes' buttons are in the tab order, in name order, right after the choice of tables.
  await page.getByTestId('er-choose-toggle').focus();
  await page.keyboard.press('Tab');
  await expect(
    page
      .getByTestId('er-canvas')
      .getByRole('button', { name: 'Show alembic_version in the explorer' }),
  ).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(
    page.getByTestId('er-canvas').getByRole('button', { name: 'Show material in the explorer' }),
  ).toBeFocused();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
  // The third table by name is the hostile one; the explorer shows it, with the focus on its tree row.
  await expect(page.getByTestId('explorer-workspace')).toBeVisible();
  await expect(page.getByTestId('explorer-view-objects')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('treeitem', { selected: true })).toBeFocused();
  await expect(page.getByRole('treeitem', { selected: true })).toContainText('notes');

  // The choice: clear it, pick two, apply.
  await page.getByTestId('explorer-view-diagram').click();
  await expect(page.getByTestId('er-box')).toHaveCount(6);
  await page.getByTestId('er-choose-toggle').click();
  await page.getByTestId('er-choose-none').click();
  await expect(page.getByTestId('er-choose-apply')).toHaveAttribute('aria-disabled', 'true');
  await page.getByRole('checkbox', { name: 'part', exact: true }).check();
  await page.getByRole('checkbox', { name: 'material', exact: true }).check();
  await page.getByTestId('er-choose-apply').click();
  await expect(page.getByTestId('er-box')).toHaveCount(2);
  await expect(page.getByTestId('er-status')).toHaveText(
    /^Tables: 2, relationships: 1, layout: \d+ ms\.$/,
  );
  await expect(page.getByTestId('er-choose-toggle')).toHaveText('Tables: 2 of 6');
  // The table alternative: part's key to material, material referenced by part.
  const list = page.getByTestId('er-list');
  await expect(
    list.getByTestId('er-list-row').filter({
      has: page.getByRole('button', { name: 'Show part in the explorer', exact: true }),
    }),
  ).toContainText('fk_part_material: (material_id) → material (id)');
  await list.getByRole('button', { name: 'Show material in the explorer' }).click();
  await expect(page.getByRole('treeitem', { name: 'material', exact: true })).toBeFocused();
});
