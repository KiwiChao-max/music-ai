import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { useAuth } from "@/contexts/AuthContext";

export function LoginPage() {
  const { t } = useTranslation();
  const { login } = useAuth();
  const navigate = useNavigate();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!identifier.trim() || !password) return;
    setError(null);
    setSubmitting(true);
    try {
      await login(identifier.trim(), password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("auth.loginError"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-sm py-16">
      <h1 className="mb-6 text-2xl font-semibold">{t("auth.loginTitle")}</h1>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label
            htmlFor="login-identifier"
            className="mb-1 block text-sm font-medium text-slate-700 dark:text-slate-300"
          >
            {t("auth.identifier")}
          </label>
          <input
            id="login-identifier"
            type="text"
            autoComplete="username"
            required
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:placeholder-slate-500"
            placeholder={t("auth.identifierPlaceholder")}
          />
        </div>
        <div>
          <label
            htmlFor="login-password"
            className="mb-1 block text-sm font-medium text-slate-700 dark:text-slate-300"
          >
            {t("auth.password")}
          </label>
          <input
            id="login-password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:placeholder-slate-500"
          />
        </div>
        {error && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
        >
          {submitting ? t("common.loading") : t("auth.login")}
        </button>
      </form>
      <p className="mt-4 text-center text-sm text-slate-500 dark:text-slate-400">
        {t("auth.noAccount")}{" "}
        <Link
          to="/register"
          className="font-medium text-slate-900 hover:underline dark:text-slate-100"
        >
          {t("auth.register")}
        </Link>
      </p>
    </div>
  );
}
