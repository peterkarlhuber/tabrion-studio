/**
 * QA-S1 S1-32: the access protection's refusals get a text of their own in every language the page ships, never the
 * fallback "The request failed (access.…)". The codes are read from the server's catalog (`problems.py`), so a new
 * `access.*` code there cannot slip through.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import i18n from '../../i18n';
import de from '../../i18n/locales/de.json';
import en from '../../i18n/locales/en.json';
import { problemText } from './messages';

const catalog = readFileSync(resolve(process.cwd(), '../src/tabrion_studio/problems.py'), 'utf8');
const ACCESS_CODES = [...new Set(catalog.match(/"access\.[a-z_]+"/g) ?? [])].map((code) =>
  code.slice(1, -1),
);

const problem = (code: string, status = 403) => ({ code, kind: 'access', status, logId: null });

describe('problemText of access.* refusals (QA-S1 S1-32)', () => {
  afterAll(async () => {
    await i18n.changeLanguage('en');
  });

  it('finds the access codes of the server catalog', () => {
    expect(ACCESS_CODES).toContain('access.csrf_invalid');
    expect(ACCESS_CODES).toContain('access.session_required');
    expect(ACCESS_CODES.length).toBeGreaterThanOrEqual(15);
  });

  for (const [lang, texts] of [
    ['en', en],
    ['de', de],
  ] as const) {
    it(`${lang}: no access code gets the general fallback`, async () => {
      await i18n.changeLanguage(lang);
      const t = i18n.t.bind(i18n);
      const fallback = texts.problems.other.split('(')[0] as string;
      for (const code of ACCESS_CODES) {
        expect(problemText(t, problem(code))).not.toContain(fallback);
      }
    });
  }

  it('says that the session ended and how to go on, like the "Session ended" notice', async () => {
    await i18n.changeLanguage('de');
    const t = i18n.t.bind(i18n);
    const text = problemText(t, problem('access.csrf_invalid'));
    expect(text).toBe(de.problems.session_ended);
    expect(text).toContain('beendet');
    expect(text).toContain('tabrion-studio erneut');
    expect(text).not.toContain('access.');
    expect(problemText(t, problem('access.session_required', 401))).toBe(text);
    await i18n.changeLanguage('en');
    expect(problemText(i18n.t.bind(i18n), problem('access.csrf_invalid'))).toBe(
      "This tab's session has ended: Studio was opened again in another tab, or restarted. Continue in the newest " +
        'Studio tab, or run tabrion-studio again to open Studio.',
    );
  });

  it('names the code of a refusal that Studio never sends its own page', async () => {
    await i18n.changeLanguage('en');
    expect(problemText(i18n.t.bind(i18n), problem('access.cross_site'))).toBe(
      'Studio refused the request because it did not come from its own page (access.cross_site). Open Studio with ' +
        'the link that tabrion-studio opens or prints.',
    );
  });
});
