import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { instrumentsApi } from "@/api/instruments";
import { ActiveSoundFontBanner } from "@/components/sampleLibrary/ActiveSoundFontBanner";
import { LibraryCard } from "@/components/sampleLibrary/LibraryCard";
import { SoundFontPanel } from "@/components/sampleLibrary/SoundFontPanel";
import { UploadCard } from "@/components/sampleLibrary/UploadCard";
import { QUERY_KEY } from "@/components/sampleLibrary/constants";

type TabKey = "samples" | "soundfonts";

export function SampleLibraryPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<TabKey>("samples");

  const librariesQuery = useQuery({
    queryKey: QUERY_KEY,
    queryFn: instrumentsApi.list,
  });
  const activeQuery = useQuery({
    queryKey: [...QUERY_KEY, "active"],
    queryFn: instrumentsApi.active,
  });
  const drumTypesQuery = useQuery({
    queryKey: ["drum-types"],
    queryFn: instrumentsApi.listDrumTypes,
  });

  const activate = useMutation({
    mutationFn: (id: number) => instrumentsApi.activate(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    },
  });
  const deactivate = useMutation({
    mutationFn: (id: number) => instrumentsApi.deactivate(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    },
  });
  const remove = useMutation({
    mutationFn: (id: number) => instrumentsApi.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    },
  });

  const activeId = activeQuery.data?.id ?? null;
  const libraries = librariesQuery.data ?? [];
  const drumTypes = drumTypesQuery.data ?? [];
  const refreshLibraries = () => queryClient.invalidateQueries({ queryKey: QUERY_KEY });

  const tabClass = (key: TabKey) =>
    `px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
      activeTab === key
        ? "border-slate-900 text-slate-900 dark:border-slate-100 dark:text-slate-100"
        : "border-transparent text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
    }`;

  return (
    <section className="space-y-8">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
          {t("samples.title")}
        </h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">{t("samples.subtitle")}</p>
      </header>

      <div className="border-b border-slate-200 dark:border-slate-800">
        <nav className="flex gap-1">
          <button
            type="button"
            className={tabClass("samples")}
            onClick={() => setActiveTab("samples")}
          >
            {t("samples.tabDrumKits")}
          </button>
          <button
            type="button"
            className={tabClass("soundfonts")}
            onClick={() => setActiveTab("soundfonts")}
          >
            {t("samples.tabSoundfonts")}
          </button>
        </nav>
      </div>

      {activeTab === "samples" && (
        <>
          <UploadCard />

          <section className="space-y-3">
            <h2 className="text-lg font-semibold tracking-tight text-slate-900 dark:text-slate-100">
              {t("samples.yourLibraries")}
            </h2>
            {librariesQuery.isLoading && (
              <p className="text-sm text-slate-500 dark:text-slate-400">{t("common.loading")}</p>
            )}
            {librariesQuery.isError && (
              <p className="text-sm text-red-600 dark:text-red-400">
                {t("samples.loadError", { message: librariesQuery.error.message })}
              </p>
            )}
            {libraries.length === 0 && !librariesQuery.isLoading && (
              <p className="rounded-md border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
                {t("samples.noLibraries")}
              </p>
            )}
            <ul className="space-y-3">
              {libraries.map((library) => (
                <LibraryCard
                  key={library.id}
                  library={library}
                  isActive={activeId === library.id}
                  onActivate={() => activate.mutate(library.id)}
                  onDeactivate={() => deactivate.mutate(library.id)}
                  onDelete={() => {
                    if (confirm(t("samples.deleteConfirm", { name: library.name }))) {
                      remove.mutate(library.id);
                    }
                  }}
                  onUpdated={refreshLibraries}
                  drumTypes={drumTypes}
                />
              ))}
            </ul>
          </section>
        </>
      )}

      {activeTab === "soundfonts" && (
        <>
          <ActiveSoundFontBanner />
          <SoundFontPanel />
        </>
      )}
    </section>
  );
}
