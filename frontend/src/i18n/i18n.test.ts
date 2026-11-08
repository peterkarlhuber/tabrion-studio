import { act } from '@testing-library/react';

import i18n, { detectLocale, matchLocale, setLocale } from './index';
import de from './locales/de.json';
import en from './locales/en.json';

type Tree = { [key: string]: string | Tree };

function flatten(tree: Tree, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === 'string') out[path] = value;
    else Object.assign(out, flatten(value, path));
  }
  return out;
}

const english = flatten(en);
const german = flatten(de);

/** Placeholders ({{name}}) and the inline tags the Trans component maps (<code>…</code>). */
function markers(text: string): string[] {
  return [...text.matchAll(/\{\{\s*(\w+)\s*\}\}|<\/?(\w+)>/g)].map((m) => m[0]).sort();
}

// Strings that are the same in both languages on purpose: names, environment labels and loan words.
const SAME_IN_BOTH = new Set([
  // Product names and technical terms that German uses as they are (STU-008).
  'connections.engine.sqlite',
  'connections.engine.postgresql',
  'connections.detail.engine',
  'connections.detail.server',
  'connections.detail.tls',
  'connections.wizard.postgresql',
  'connections.wizard.host',
  'connections.wizard.port',
  'connections.wizard.name',
  'dialogs.trustProject.heading',
  'dialogs.trustProject.flowTls',
  'app.name',
  'app.title',
  'app.connectionTitle',
  'nav.start',
  'header.label',
  'header.env.dev',
  'header.env.test',
  'header.env.prod',
  'languages.en',
  'languages.de',
  'start.logoAlt',
  'settings.themeSystem',
  'settings.densityNormal',
  'dialogs.trustProject.hosts',
]);

describe('translations (de and en)', () => {
  it('have the same keys', () => {
    expect(Object.keys(german).sort()).toEqual(Object.keys(english).sort());
  });

  it('have no empty strings', () => {
    for (const [key, value] of [...Object.entries(english), ...Object.entries(german)]) {
      expect(value.trim(), key).not.toBe('');
    }
  });

  it.each(Object.keys(english))('%s keeps the placeholders and tags', (key) => {
    expect(markers(german[key] as string)).toEqual(markers(english[key] as string));
  });

  it('translate every text that is not a name', () => {
    const untranslated = Object.keys(english).filter(
      (key) => english[key] === german[key] && !SAME_IN_BOTH.has(key),
    );
    expect(untranslated).toEqual([]);
  });

  it('use typographic quotes and ellipses in German, not ASCII stand-ins', () => {
    for (const [key, value] of Object.entries(german)) {
      expect(value.includes('...'), key).toBe(false);
      expect(/"/.test(value), key).toBe(false);
    }
  });
});

describe('locale detection', () => {
  afterEach(async () => {
    window.localStorage.clear();
    await act(async () => {
      await i18n.changeLanguage('en');
    });
  });

  it('maps browser tags to the supported languages', () => {
    expect(matchLocale('de-AT')).toBe('de');
    expect(matchLocale('en_US')).toBe('en');
    expect(matchLocale('fr-FR')).toBeNull();
    expect(detectLocale(['fr-FR', 'de-CH'])).toBe('de');
    expect(detectLocale(['es-ES'])).toBe('en');
  });

  it('prefers the stored choice and sets the document language', async () => {
    await act(async () => {
      await setLocale('de');
    });
    expect(detectLocale(['en-US'])).toBe('de');
    expect(document.documentElement.lang).toBe('de');
    expect(i18n.t('nav.settings')).toBe('Einstellungen');
  });
});
