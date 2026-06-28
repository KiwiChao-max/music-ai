/**
 * i18n bootstrap.
 *
 * Wired up once at the top of `main.tsx`. The default language is
 * detected from the browser, then persisted to `localStorage` so
 * the choice survives reloads. All user-facing strings live in
 * `locales/{lang}.json`; everything else stays in code.
 *
 * Why a key-based t() function and not a fancy Compile approach?
 * We want the bundle small, the dev loop fast, and the keys human
 * readable. `react-i18next` gives us all of that.
 */
import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";
import zh from "./locales/zh.json";

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      zh: { translation: zh },
    },
    fallbackLng: "en",
    // The detector checks localStorage first, then the browser
    // language. We use the same key as the rest of the app so the
    // theme and language preferences live in one place.
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: "music-ai.lang",
      caches: ["localStorage"],
    },
    interpolation: { escapeValue: false },
  });

export const SUPPORTED_LANGUAGES = ["en", "zh"] as const;
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];

export default i18n;
