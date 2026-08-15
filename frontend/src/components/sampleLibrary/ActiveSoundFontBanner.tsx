import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";

import { instrumentsApi } from "@/api/instruments";

export function ActiveSoundFontBanner() {
  const { t } = useTranslation();
  // No refetchInterval here: the active SoundFont only changes through
  // user actions on this page (import / activate / delete), and every one
  // of those invalidates ["soundfonts"], which refetches this banner too.
  // Polling every 30s would just burn a request for data that can't have
  // changed on its own.
  const activeQuery = useQuery({
    queryKey: ["soundfonts", "active"],
    queryFn: instrumentsApi.activeSoundFont,
  });
  if (activeQuery.isLoading) {
    return (
      <div
        className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900"
        aria-label={t("common.loading")}
      >
        <p className="text-sm text-slate-500 dark:text-slate-400">{t("common.loading")}</p>
      </div>
    );
  }
  if (!activeQuery.data) {
    return (
      <div className="rounded-lg border border-dashed border-amber-300 bg-amber-50/60 p-4 dark:border-amber-800 dark:bg-amber-950/20">
        <p className="text-sm font-medium text-amber-900 dark:text-amber-200">
          {t("samples.soundfontInactive")}
        </p>
        <p className="mt-1 text-xs text-amber-800/80 dark:text-amber-300/80">
          {t("samples.soundfontInactiveHint")}
        </p>
      </div>
    );
  }
  const sf = activeQuery.data;
  return (
    <div className="rounded-lg border border-emerald-200 bg-emerald-50/60 p-4 dark:border-emerald-800 dark:bg-emerald-950/20">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-emerald-900 dark:text-emerald-200">
            {t("samples.soundfontActive", { name: sf.name })}
          </p>
          <p className="mt-1 text-xs text-emerald-800/80 dark:text-emerald-300/80">
            {t("samples.soundfontActiveHint", { count: sf.preset_count })}
          </p>
        </div>
        <span className="rounded-full bg-emerald-100 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">
          {t("samples.active")}
        </span>
      </div>
    </div>
  );
}
