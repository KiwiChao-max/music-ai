import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { ProgressBar } from "@/components/ProgressBar";
import { Skeleton, EmptyState, ErrorState } from "@/components/States";
import { StatusBadge } from "@/components/StatusBadge";
import { useAudioTasks } from "@/hooks/useAudioTasks";

export function AudioListPage() {
  const { data: tasks, isLoading, isError, error, refetch } = useAudioTasks();
  const { t } = useTranslation();

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
            {t("tasks.title")}
          </h1>
          <p className="text-sm text-slate-600 dark:text-slate-400">{t("tasks.subtitle")}</p>
        </div>
        <Link
          to="/upload"
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
        >
          {t("tasks.newUpload")}
        </Link>
      </div>

      {isLoading && (
        <ul className="space-y-2" aria-label="Loading tasks">
          {Array.from({ length: 3 }).map((_, idx) => (
            <li
              key={idx}
              className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900"
            >
              <Skeleton width="w-1/2" />
              <Skeleton width="w-20" height="h-5" />
            </li>
          ))}
        </ul>
      )}

      {isError && <ErrorState error={error} onRetry={() => refetch()} />}

      {tasks && tasks.length === 0 && (
        <EmptyState
          title={t("tasks.empty.title")}
          description={t("tasks.empty.description")}
          action={
            <Link
              to="/upload"
              className="inline-flex items-center justify-center rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
            >
              {t("tasks.empty.cta")}
            </Link>
          }
        />
      )}

      {tasks && tasks.length > 0 && (
        <ul className="divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200 bg-white dark:divide-slate-800 dark:border-slate-800 dark:bg-slate-900">
          {tasks.map((t) => (
            <li key={t.id}>
              <Link
                to={`/audio/${t.id}`}
                className="block px-4 py-3 transition-colors hover:bg-slate-50 dark:hover:bg-slate-800"
              >
                <div className="flex items-center justify-between gap-3">
                  <p className="min-w-0 flex-1 truncate text-sm font-medium text-slate-900 dark:text-slate-100">
                    #{t.id} · {t.filename}
                  </p>
                  <StatusBadge status={t.status} />
                </div>
                {t.status === "PROCESSING" && (
                  <div className="mt-2">
                    <ProgressBar value={t.progress} />
                    {t.current_step && (
                      <p className="mt-1 truncate text-xs text-slate-500 dark:text-slate-400">
                        {t.current_step}
                      </p>
                    )}
                  </div>
                )}
                {t.status === "FAILED" && t.error_message && (
                  <p className="mt-1 truncate text-xs text-red-600 dark:text-red-400">
                    {t.error_message}
                  </p>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
