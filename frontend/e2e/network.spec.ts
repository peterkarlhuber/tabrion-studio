/**
 * QA-9 and SQ-08 (07 section 2): the frontend makes no request to any other host. No CDN font, no telemetry, no
 * external image or script: every request of every view, dialog and notice goes to the page's own origin. The
 * real-server check is in backend.spec.ts ("no request leaves the local server").
 */
import { test, expect } from '@playwright/test';

import { foreignRequests, openHarness } from './support';

const VISITS: { scenario?: string; path?: string; theme?: 'light' | 'dark'; lang?: 'en' | 'de' }[] =
  [
    {},
    { path: '/connections' },
    { path: '/settings', theme: 'dark', lang: 'de' },
    { path: '/nowhere' },
    { scenario: 'prod' },
    { scenario: 'test' },
    { scenario: 'dialog-d01-hostile' },
    { scenario: 'dialog-d02' },
    { scenario: 'dialog-d06' },
    { scenario: 'dialog-d08' },
    { scenario: 'project-invalid' },
    { scenario: 'used' },
    { scenario: 'ended' },
  ];

test('no view, dialog or notice requests another host (SQ-08)', async ({ page, baseURL }) => {
  const foreign = foreignRequests(page, () => baseURL ?? '');
  let requests = 0;
  page.on('request', () => {
    requests += 1;
  });
  for (const visit of VISITS) {
    // The theme and language persist in the browser: name them for every visit.
    await openHarness(page, { theme: 'light', lang: 'en', ...visit });
    await page.waitForLoadState('networkidle');
  }
  expect(requests).toBeGreaterThan(VISITS.length); // the listener really saw the page's requests
  expect(foreign).toEqual([]);
});
