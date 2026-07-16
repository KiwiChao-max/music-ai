import { useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { LanguageToggle } from "@/components/LanguageToggle";
import { PlayerBar } from "@/components/PlayerBar";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useAuth } from "@/contexts/AuthContext";

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  `px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
    isActive
      ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
      : "text-slate-600 hover:text-slate-900 hover:bg-slate-100 dark:text-slate-300 dark:hover:text-slate-100 dark:hover:bg-slate-800"
  }`;

export function MainLayout() {
  const { t } = useTranslation();
  const { user, isAuthenticated, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);

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
            {isAuthenticated ? (
              <div className="relative">
                <button
                  onClick={() => setMenuOpen((v) => !v)}
                  className="flex items-center gap-1.5 rounded-md px-2 py-1 text-sm font-medium text-slate-600 hover:text-slate-900 hover:bg-slate-100 dark:text-slate-300 dark:hover:text-slate-100 dark:hover:bg-slate-800"
                >
                  <span className="inline-block h-6 w-6 rounded-full bg-slate-200 text-center text-xs leading-6 dark:bg-slate-700">
                    {(user?.username ?? "?")[0].toUpperCase()}
                  </span>
                  <span className="hidden sm:inline">{user?.username}</span>
                </button>
                {menuOpen && (
                  <>
                    <div
                      className="fixed inset-0 z-10"
                      onClick={() => setMenuOpen(false)}
                    />
                    <div className="absolute right-0 top-full z-20 mt-1 w-48 rounded-lg border border-slate-200 bg-white py-1 shadow-lg dark:border-slate-700 dark:bg-slate-900">
                      <div className="border-b border-slate-100 px-4 py-2 text-sm text-slate-500 dark:border-slate-800 dark:text-slate-400">
                        {user?.email}
                      </div>
                      <button
                        onClick={() => {
                          setMenuOpen(false);
                          logout();
                        }}
                        className="w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800"
                      >
                        {t("auth.logout")}
                      </button>
                    </div>
                  </>
                )}
              </div>
            ) : (
              <Link
                to="/login"
                className="rounded-md px-3 py-1.5 text-sm font-medium text-slate-600 hover:text-slate-900 hover:bg-slate-100 dark:text-slate-300 dark:hover:text-slate-100 dark:hover:bg-slate-800"
              >
                {t("auth.login")}
              </Link>
            )}
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