/**
 * QA-S1 section B, "Explorer und ER-Diagramm": the object tree and its filter, the detail tabs of `orders`, the row
 * count, the ER diagram with its controls, and its image export.
 */
import { readFileSync } from 'node:fs';

import { expect, openInExplorer, skipWithoutStudio, startSession, test } from './studio';

skipWithoutStudio();

const DEV = 'Shop (DEV)';

test('S1-05 the tree shows tables and views; "Objekte filtern" with "ord" leaves orders and order_items', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await openInExplorer(page, DEV);
  const tree = page.getByRole('tree');
  // The views group starts collapsed; open it as a user would.
  await tree.getByRole('treeitem', { name: /^Views \(\d+\)$/ }).click();
  for (const name of ['customers', 'orders', 'order_items', 'monthly_revenue', 'top_customers']) {
    await expect(tree.getByRole('treeitem', { name, exact: true })).toBeVisible();
  }
  await page.getByLabel('Objekte filtern', { exact: true }).fill('ord');
  // Only the leaves (the objects) are compared; group rows such as "Tabellen" may stay.
  const names = [
    'customers',
    'products',
    'orders',
    'order_items',
    'monthly_revenue',
    'top_customers',
  ];
  await expect(tree.getByRole('treeitem', { name: 'orders', exact: true })).toBeVisible();
  await expect(tree.getByRole('treeitem', { name: 'order_items', exact: true })).toBeVisible();
  for (const name of names.filter((n) => !['orders', 'order_items'].includes(n))) {
    await expect(tree.getByRole('treeitem', { name, exact: true })).toHaveCount(0);
  }
});

test('S1-06 the tabs of orders: columns, constraints, indexes, relationships both ways and the DDL', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await openInExplorer(page, DEV);
  await page.getByRole('treeitem', { name: 'orders', exact: true }).click();
  const detail = page.getByTestId('explorer-detail');
  for (const tab of ['Spalten', 'Constraints', 'Indizes', 'Beziehungen', 'DDL']) {
    await expect(detail.getByRole('tab', { name: tab, exact: true })).toBeVisible();
  }
  await detail.getByRole('tab', { name: 'Spalten', exact: true }).click();
  await expect(detail.getByRole('tabpanel')).toContainText('order_date');
  await detail.getByRole('tab', { name: 'Constraints', exact: true }).click();
  await expect(detail.getByRole('tabpanel')).toBeVisible();
  await detail.getByRole('tab', { name: 'Indizes', exact: true }).click();
  await expect(detail.getByRole('tabpanel')).toBeVisible();
  await detail.getByRole('tab', { name: 'Beziehungen', exact: true }).click();
  // What orders points to (customers) and who points to orders (order_items).
  await expect(
    detail.getByTestId('detail-references').filter({ hasText: 'customers' }),
  ).toHaveCount(1);
  await expect(
    detail.getByTestId('detail-referenced-by').filter({ hasText: 'order_items' }),
  ).toHaveCount(1);
  await detail.getByRole('tab', { name: 'DDL', exact: true }).click();
  await expect(page.getByTestId('ddl-text')).toContainText('CREATE TABLE orders');
});

test('S1-07 "Zeilen zählen" on orders shows the number of rows and how long counting took', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await openInExplorer(page, DEV);
  await page.getByRole('treeitem', { name: 'orders', exact: true }).click();
  await page.getByRole('button', { name: 'Zeilen zählen', exact: true }).click();
  // The demo has 1500 orders; German digit grouping.
  await expect(page.getByTestId('row-count-result')).toHaveText(
    /^1\.500 Zeilen \(gezählt in [\d.,]+ ms\)\.$/,
  );
});

test('S1-08 the ER diagram: boxes with columns, key lines, zoom, pan, a minimap and the relationships as text', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await openInExplorer(page, DEV);
  await page.getByRole('button', { name: 'ER-Diagramm', exact: true }).click();
  const canvas = page.getByTestId('er-canvas');
  await expect(page.getByTestId('er-status')).toHaveText(
    /^Tabellen: \d+, Beziehungen: \d+, Layout: \d+ ms\.$/,
  );
  const status = (await page.getByTestId('er-status').textContent()) ?? '';
  const [, tables, lines] = /Tabellen: (\d+), Beziehungen: (\d+)/.exec(status) ?? [];
  await expect(page.getByTestId('er-box')).toHaveCount(Number(tables));
  expect(Number(lines)).toBeGreaterThan(0);
  await expect(canvas.locator('.react-flow__edge-path')).toHaveCount(Number(lines));
  // A box shows the table's columns.
  await expect(page.getByTestId('er-box').filter({ hasText: 'orders' }).first()).toContainText(
    'order_date',
  );
  const viewport = canvas.locator('.react-flow__viewport');
  const transform = () => viewport.evaluate((node) => getComputedStyle(node).transform);
  const before = await transform();
  await page.getByRole('button', { name: 'Vergrößern', exact: true }).click();
  await expect.poll(transform).not.toBe(before);
  const zoomed = await transform();
  await page.getByRole('button', { name: 'Verkleinern', exact: true }).click();
  await expect.poll(transform).not.toBe(zoomed);
  await page.getByRole('button', { name: 'An Ansicht anpassen', exact: true }).click();
  const fitted = await transform();
  // Pan: drag the background, from its top right corner (the zoom buttons are top left, the minimap bottom right).
  await canvas.scrollIntoViewIfNeeded();
  const box = await canvas.boundingBox();
  expect(box).not.toBeNull();
  if (box) {
    await page.mouse.move(box.x + box.width - 20, box.y + 20);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width - 140, box.y + 80, { steps: 8 });
    await page.mouse.up();
  }
  await expect.poll(transform).not.toBe(fitted);
  await expect(canvas.getByLabel('Übersicht des Diagramms')).toBeVisible();
  // The table under the diagram lists the same relationships as text.
  const list = page.getByTestId('er-list');
  await expect(list).toBeVisible();
  await expect(page.getByTestId('er-list-row')).toHaveCount(Number(tables));
  await expect(list).toContainText('Verweist auf');
});

test('S1-09 a click on products in the diagram shows it in the explorer; "Als PNG speichern" downloads the diagram', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await openInExplorer(page, DEV);
  await page.getByRole('button', { name: 'ER-Diagramm', exact: true }).click();
  await expect(page.getByTestId('er-box')).not.toHaveCount(0);
  for (const [name, suffix] of [
    ['Als PNG speichern', 'png'],
    ['Als SVG speichern', 'svg'],
  ] as const) {
    const saved = page.waitForEvent('download');
    await page.getByRole('button', { name, exact: true }).click();
    const download = await saved;
    expect(download.suggestedFilename()).toMatch(new RegExp(`\\.${suffix}$`));
    const body = readFileSync(await download.path());
    if (suffix === 'png') expect([...body.subarray(1, 4)]).toEqual([0x50, 0x4e, 0x47]);
    // The whole diagram: every table of the schema is in the SVG.
    else
      for (const table of ['customers', 'orders', 'order_items', 'products'])
        expect(body.toString('utf8')).toContain(`>${table}<`);
  }
  // A mouse click on the table's name in the canvas, where a user clicks (not the list under the diagram).
  const name = page
    .getByTestId('er-canvas')
    .getByRole('button', { name: 'products im Explorer zeigen', exact: true });
  await name.scrollIntoViewIfNeeded();
  const box = await name.boundingBox();
  expect(box).not.toBeNull();
  if (box) await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await expect(page.getByRole('treeitem', { name: 'products', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.getByTestId('explorer-detail')).toContainText('price_cents');
});
