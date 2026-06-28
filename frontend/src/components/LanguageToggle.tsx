/**
 * Language switcher.
 *
 * Toggles between English and Chinese. The current language is
 * stored in `localStorage` (via the i18n language detector) and
 * read back on the next page load, so the choice survives reloads
 * and other tabs.
 */
import { useTranslation } from "react-i18next";

export function LanguageToggle() {
  const { i18n, t } = useTranslation();
  const isZh = i18n.language?.startsWith("zh") ?? false;
  const label = isZh ? t("player.switchToEnglish") : t("player.switchToChinese");

  const toggle = () => {
    void i18n.changeLanguage(isZh ? "en" : "zh");
  };

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={label}
      title={label}
      className="inline-flex h-9 items-center justify-center gap-1 rounded-md border border-slate-200 bg-white px-2 text-xs font-semibold uppercase tracking-wide text-slate-700 transition-colors hover:bg-slate-100 hover:text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700 dark:hover:text-slate-50"
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <circle cx="12" cy="12" r="10" />
        <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
      </svg>
      {isZh ? "EN" : "中"}
    </button>
  );
}
