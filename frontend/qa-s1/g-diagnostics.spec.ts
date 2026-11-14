/**
 * QA-S1 section G, "Diagnose und Bericht": the findings by severity with a symbol and a word, the backups section,
 * and the Markdown report without paths, the database's file name or passwords.
 */
import { readFileSync } from 'node:fs';

import type { Page } from '@playwright/test';

import { chooseProfile, expect, goTo, skipWithoutStudio, startSession, test } from './studio';

skipWithoutStudio();

const SEVERITIES = ['Fehler', 'Warnung', 'Hinweis'];

async function runDiagnosis(page: Page): Promise<void> {
  await goTo(page, 'Diagnose');
  await chooseProfile(page, 'Verbindungsprofil', 'Shop (DEV)');
  await page.getByRole('button', { name: 'Diagnose starten', exact: true }).click();
  await expect(page.getByTestId('diagnostics-result')).toBeVisible();
}

test('S1-28 the findings are sorted by severity, each with a symbol and a word, an explanation and a remedy; then "Sicherungen"', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await runDiagnosis(page);
  const findings = page.getByTestId('diagnostics-findings').getByTestId('diagnostics-finding');
  const count = await findings.count();
  const words: string[] = [];
  for (let index = 0; index < count; index += 1) {
    const finding = findings.nth(index);
    const severity = finding.getByTestId('diagnostics-severity');
    const word = ((await severity.textContent()) ?? '').trim();
    expect(SEVERITIES, `finding ${index + 1}: severity word "${word}"`).toContain(word);
    words.push(word);
    // A symbol next to the word.
    await expect(severity.locator('svg')).toHaveCount(1);
    await expect(finding.getByTestId('diagnostics-detail')).not.toBeEmpty();
    await expect(finding.getByTestId('diagnostics-remedy')).not.toBeEmpty();
  }
  const order = words.map((word) => SEVERITIES.indexOf(word));
  expect(order).toEqual([...order].sort((a, b) => a - b));
  const backups = page.getByRole('region', { name: 'Sicherungen' });
  await expect(backups).toBeVisible();
  // "Sicherungen" comes after the findings.
  const below = await page.evaluate(() => {
    const list = document.querySelector('[data-testid="diagnostics-findings"]');
    const backup = document.querySelector('[data-testid="diagnostics-backup"]');
    if (!list || !backup) return null;
    return Boolean(list.compareDocumentPosition(backup) & Node.DOCUMENT_POSITION_FOLLOWING);
  });
  expect(below).toBe(true);
});

test('S1-29 "Bericht als Markdown herunterladen" saves an English report without paths, file names or passwords', async ({
  page,
  studio,
}) => {
  await startSession(page, studio);
  await runDiagnosis(page);
  const saved = page.waitForEvent('download');
  await page
    .getByRole('button', { name: 'Bericht als Markdown herunterladen', exact: true })
    .click();
  const download = await saved;
  expect(download.suggestedFilename()).toMatch(/^tabrion-studio-diagnosis-.+\.md$/);
  const body = readFileSync(await download.path(), 'utf8');
  expect(body.length).toBeGreaterThan(0);
  // English on purpose: none of the page's German severity words or headings.
  expect(body).not.toMatch(/\b(Fehler|Warnung|Hinweis|Sicherungen|Befund)\b/);
  for (const secret of [
    studio.root,
    studio.project,
    'shop.sqlite',
    'shop-prod.sqlite',
    'daten/',
    'beispielprojekt',
  ]) {
    expect(body, `the report names ${secret}`).not.toContain(secret);
  }
  expect(body).not.toMatch(/password\s*[:=]\s*\S+/i);
});
