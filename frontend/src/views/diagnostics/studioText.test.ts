/**
 * TD-032: the page words Studio's details and remedies from their params. The English wording of the shared cases is
 * the server's (`studioTexts.cases.json`, checked against `services/diagnosis_texts.py` in
 * `tests/unit/test_diagnostics_page.py`); German words every case with every placeholder filled; params that do not
 * fit are not worded (the page then shows the server's English text).
 */
import type { Finding } from '../../api/diagnostics';
import i18n from '../../i18n';
import cases from './studioTexts.cases.json';
import { studioText } from './studioText';

function studio(code: string, params: Record<string, unknown> | null): Finding {
  return { code, severity: 'info', title: '', detail: '', remedy: '', source: 'studio', params };
}

const en = i18n.getFixedT('en');
const de = i18n.getFixedT('de');

describe('Studio texts (TD-032)', () => {
  it.each(cases.map((item) => [item.code, item] as const))(
    '%s in English is the server text',
    (_code, item) => {
      expect(studioText(en, studio(item.code, item.params))).toEqual({
        detail: item.detail,
        remedy: item.remedy,
      });
    },
  );

  it.each(cases.map((item) => [item.code, item] as const))(
    '%s has German text with every placeholder filled',
    (_code, item) => {
      const german = studioText(de, studio(item.code, item.params));
      expect(german).not.toBeNull();
      for (const text of [german?.detail ?? '', german?.remedy ?? '']) {
        expect(text.trim()).not.toBe('');
        expect(text).not.toMatch(/\{\{|\}\}|diagnostics\.texts/);
      }
      expect(german?.detail).not.toBe(item.detail);
    },
  );

  it('covers every code the page titles', () => {
    const titled = Object.keys(
      (i18n.getResourceBundle('en', 'common') as { diagnostics: { titles: object } }).diagnostics
        .titles,
    );
    expect([...new Set(cases.map((item) => item.code))].sort()).toEqual(titled.sort());
  });

  it('uses German plural forms', () => {
    expect(studioText(de, studio('STU-DX-005', { rows: 1 }))?.detail).toContain('1 Zeile,');
    expect(studioText(de, studio('STU-DX-005', { rows: 1000 }))?.detail).toContain('1000+ Zeilen,');
    expect(studioText(de, studio('STU-BK-002', { amount: 1, unit: 'days' }))?.detail).toContain(
      'ist 1 Tag älter',
    );
  });

  it.each([
    ['tabrion finding', { ...studio('STU-DX-005', { rows: 1 }), source: 'tabrion' as const }],
    ['no params', studio('STU-DX-005', null)],
    ['unknown code', studio('STU-DX-099', {})],
    ['sslmode not a mode', studio('STU-DX-001', { sslmode: 'prefer; DROP' })],
    ['weak TLS without sslmode', studio('STU-DX-002', {})],
    ['count as text', studio('STU-DX-003', { writable: '2', creatable: 0, temporary: false })],
    ['negative count', studio('STU-DX-005', { rows: -1 })],
    ['fractional count', studio('STU-DX-005', { rows: 1.5 })],
    ['prod not a boolean', studio('STU-DX-006', { owned: 3, prod: 'yes' })],
    ['unknown reason', studio('STU-DX-008', { reason: 'x', time_limit_s: 1, skipped: ['backup'] })],
    ['unknown step', studio('STU-DX-008', { reason: 'canceled', time_limit_s: 1, skipped: ['x'] })],
    ['no steps', studio('STU-DX-008', { reason: 'canceled', time_limit_s: 1, skipped: [] })],
    [
      'time limit not a number',
      studio('STU-DX-008', { reason: 'time_limit', time_limit_s: '1', skipped: ['backup'] }),
    ],
    ['unknown cause', studio('STU-DX-009', { cause: 'other' })],
    ['unknown setting', studio('STU-DX-010', { missing: ['work_mem'] })],
    ['settings not a list', studio('STU-DX-010', { missing: 'lock_timeout' })],
    ['too many settings', studio('STU-DX-010', { missing: Array(21).fill('lock_timeout') })],
    ['unknown unit', studio('STU-BK-002', { amount: 2, unit: 'weeks' })],
    ['files as text', studio('STU-BK-003', { files: 'many' })],
    ['entries missing', studio('STU-BK-005', {})],
    ['failures missing', studio('STU-BK-011', {})],
  ])('does not word %s', (_name, finding) => {
    expect(studioText(en, finding)).toBeNull();
  });
});
