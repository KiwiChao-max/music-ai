import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { useAuth } from "@/contexts/AuthContext";

export function RegisterPage() {
  const { t } = useTranslation();
  const { register } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !username.trim() || !password) return;
    if (password.length < 8) {
      setError(t("auth.passwordTooShort"));
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await register(
        email.trim(),
        username.trim(),
        password,
        fullName.trim() || undefined,
      );
      navigate("/", { replace: true });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("auth.registerError"),
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-sm py-16">
      <h1 className="mb-6 text-2xl font-semibold">{t("auth.registerTitle")}</h1>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label
            htmlFor="reg-email"
            className="mb-1 block text-sm font-medium text-slate-700 dark:text-slate-300"
          >
            {t("auth.email")}
          </label>
          <input
            id="reg-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:placeholder-slate-500"
            placeholder={t("auth.emailPlaceholder")}
          />
        </div>
        <div>
          <label
            htmlFor="reg-username"
            className="mb-1 block text-sm font-medium text-slate-700 dark:text-slate-300"
          >
            {t("auth.username")}
          </label>
          <input
            id="reg-username"
            type="text"
            autoComplete="username"
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:placeholder-slate-500"
            placeholder={t("auth.usernamePlaceholder")}
          />
        </div>
        <div>
          <label
            htmlFor="reg-password"
            className="mb-1 block text-sm font-medium text-slate-700 dark:text-slate-300"
          >
            {t("auth.password")}
          </label>
          <input
            id="reg-password"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:placeholder-slate-500"
          />
        </div>
        <div>
          <label
            htmlFor="reg-fullname"
            className="mb-1 block text-sm font-medium text-slate-700 dark:text-slate-300"
          >
            {t("auth.fullName")}{" "}
            <span className="font-normal text-slate-400">
              ({t("auth.optional")})
            </span>
          </label>
          <input
            id="reg-fullname"
            type="text"
            autoComplete="name"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder-slate-400 focus:border-slate-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:placeholder-slate-500"
            placeholder={t("auth.fullNamePlaceholder")}
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
          {submitting ? t("common.loading") : t("auth.register")}
        </button>
      </form>
      <p className="mt-4 text-center text-sm text-slate-500 dark:text-slate-400">
        {t("auth.hasAccount")}{" "}
        <Link
          to="/login"
          className="font-medium text-slate-900 hover:underline dark:text-slate-100"
        >
          {t("auth.login")}
        </Link>
      </p>
    </div>
  );
}