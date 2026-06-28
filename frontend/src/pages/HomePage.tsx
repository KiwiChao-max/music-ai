import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

export function HomePage() {
  const { t } = useTranslation();
  const cards = [
    {
      to: "/upload",
      titleKey: "home.cards.upload.title",
      descriptionKey: "home.cards.upload.description",
      ctaKey: "home.cards.upload.cta",
    },
    {
      to: "/audio",
      titleKey: "home.cards.tasks.title",
      descriptionKey: "home.cards.tasks.description",
      ctaKey: "home.cards.tasks.cta",
    },
  ];
  return (
    <section className="space-y-10">
      <header className="space-y-3">
        <h1 className="text-3xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
          {t("home.title")}
        </h1>
        <p className="max-w-prose text-slate-600 dark:text-slate-400">{t("home.tagline")}</p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2">
        {cards.map((c) => (
          <Link
            key={c.to}
            to={c.to}
            className="group block rounded-lg border border-slate-200 bg-white p-6 transition-colors hover:border-slate-400 hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-900 dark:hover:border-slate-600 dark:hover:bg-slate-800"
          >
            <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
              {t(c.titleKey)}
            </h2>
            <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
              {t(c.descriptionKey)}
            </p>
            <p className="mt-4 text-sm font-medium text-slate-900 group-hover:underline dark:text-slate-100">
              {t(c.ctaKey)} →
            </p>
          </Link>
        ))}
      </div>
    </section>
  );
}
