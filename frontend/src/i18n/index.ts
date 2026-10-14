import i18n from 'i18next';
import en from './locales/en.json';
import es from './locales/es.json';

export const SUPPORTED_LANGUAGES = ['en', 'es'] as const;
export type AppLanguage = (typeof SUPPORTED_LANGUAGES)[number];
export const LANGUAGE_STORAGE_KEY = 'hoja_language';

export function storedLanguage(): AppLanguage {
  const saved = localStorage.getItem(LANGUAGE_STORAGE_KEY) ?? '';
  return (SUPPORTED_LANGUAGES as readonly string[]).includes(saved) ? (saved as AppLanguage) : 'en';
}

// English source text is the key. Missing Spanish entries fall back to English.
void i18n.init({
  resources: {en: {translation: en}, es: {translation: es}},
  lng: storedLanguage(),
  fallbackLng: 'en',
  keySeparator: false,
  nsSeparator: false,
  interpolation: {escapeValue: false},
});

document.documentElement.lang = i18n.language;

export default i18n;

export const tr = (text: string, options?: Record<string, unknown>): string => i18n.t(text, options);
