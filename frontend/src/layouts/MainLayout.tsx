import { NavLink, Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { LanguageToggle } from "@/components/LanguageToggle";
import { PlayerBar } from "@/components/PlayerBar";
import { ThemeToggle } from "@/components/ThemeToggle";

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  `px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
    isActive
      ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
      : "text-slate-600 hover:text-slate-900 hover:bg-slate-100 dark:text-slate-300 dark:hover:text-slate-100 dark:hover:bg-slate-800"
  }`;

export function MainLayout() {
  const { t } = useTranslation();
  return (
    <div className="min-h-screen flex flex-col bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <header className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto max-w-5xl flex items-center gap-6 px-6 py-3">
          <span className="text-lg font-semibold tracking-tight">{t("app.title")}</span>
          <nav className="flex items-center gap-1">
            <NavLink to="/" end className={navLinkClass}>
              {t("app.nav.home")}
            </NavLink>
            <NavLink to="/upload" className={navLinkClass}>
              {t("app.nav.upload")}
            </NavLink>
            <NavLink to="/audio" className={navLinkClass}>
              {t("app.nav.tasks")}
            </NavLink>
            <NavLink to="/instruments" className={navLinkClass}>
              {t("app.nav.samples")}
            </NavLink>
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <LanguageToggle />
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main className="flex-1 mx-auto w-full max-w-5xl px-6 py-8 pb-32">
        <Outlet />
      </main>
      <PlayerBar />
    </div>
  );
}
