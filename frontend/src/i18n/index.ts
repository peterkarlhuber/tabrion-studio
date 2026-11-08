/**
 * UI languages (STU-005, 08: English is the source language, German is complete; es/fr/it/pt-BR are a later
 * extension, STU-027). Both languages are small and bundled. `i18n.test.ts` checks that they have the same keys and
 * the same placeholders.
 */
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import { readPreference, writePreference } from '../lib/storage';
import de from './locales/de.json';
import en from './locales/en.json';

export const SUPPORTED_LOCALES = ['en', 'de'] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];

export const resources = { en: { common: en }, de: { common: de } } as const;

export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && (SUPPORTED_LOCALES as readonly string[]).includes(value);
}

/** Map a BCP 47 tag ("de-AT", "en_US") to a supported locale, or null. */
export function matchLocale(tag: string | null | undefined): Locale | null {
  if (!tag) return null;
  const base = tag.toLowerCase().split(/[-_]/)[0];
  return isLocale(base) ? base : null;
}

/** The stored choice, else the first supported browser language, else English. */
export function detectLocale(languages: readonly string[] = browserLanguages()): Locale {
  const stored = matchLocale(readPreference('locale'));
  if (stored) return stored;
  for (const tag of languages) {
    const match = matchLocale(tag);
    if (match) return match;
  }
  return 'en';
}

function browserLanguages(): readonly string[] {
  return typeof navigator !== 'undefined' ? (navigator.languages ?? [navigator.language]) : [];
}

export function currentLocale(): Locale {
  return matchLocale(i18n.language) ?? 'en';
}

export function setLocale(locale: Locale): Promise<unknown> {
  writePreference('locale', locale);
  return i18n.changeLanguage(locale);
}

void i18n.use(initReactI18next).init({
  resources,
  lng: detectLocale(),
  fallbackLng: 'en',
  supportedLngs: [...SUPPORTED_LOCALES],
  ns: ['common'],
  defaultNS: 'common',
  // React escapes text itself; translations are never rendered as HTML (threat T-07).
  interpolation: { escapeValue: false },
  returnNull: false,
  react: { useSuspense: false },
  initAsync: false,
});

const syncDocumentLanguage = (lng: string) => {
  if (typeof document !== 'undefined') document.documentElement.lang = matchLocale(lng) ?? 'en';
};
syncDocumentLanguage(i18n.language);
i18n.on('languageChanged', syncDocumentLanguage);

export default i18n;
