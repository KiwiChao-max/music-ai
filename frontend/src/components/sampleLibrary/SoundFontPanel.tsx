import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { instrumentsApi } from "@/api/instruments";
import { FilePicker } from "./FilePicker";

export function SoundFontPanel() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const csvInputRef = useRef<HTMLInputElement | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [sf2File, setSf2File] = useState<File | null>(null);
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"list" | "sf2" | "csv" | "gm">("list");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [filterType, setFilterType] = useState<"all" | "sf2" | "preset_table">("all");
  const [presetSearch, setPresetSearch] = useState("");
  const [gmSearch, setGmSearch] = useState("");

  const sfQuery = useQuery({
    queryKey: ["soundfonts"],
    queryFn: instrumentsApi.listSoundFonts,
  });

  const filteredSoundfonts = useMemo(() => {
    if (!sfQuery.data) return [];
    return sfQuery.data.filter((sf) => {
      if (filterType !== "all" && sf.type !== filterType) return false;
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        if (!sf.name.toLowerCase().includes(q)) return false;
      }
      return true;
    });
  }, [sfQuery.data, searchQuery, filterType]);

  const detailQuery = useQuery({
    queryKey: ["soundfonts", selectedId],
    queryFn: () => instrumentsApi.getSoundFont(selectedId!),
    enabled: selectedId !== null,
  });

  const gmInstrumentsQuery = useQuery({
    queryKey: ["gm-instruments"],
    queryFn: instrumentsApi.listGmInstruments,
  });

  const filteredPresets = useMemo(() => {
    if (!detailQuery.data?.presets) return [];
    if (!presetSearch.trim()) return detailQuery.data.presets;
    const q = presetSearch.toLowerCase();
    return detailQuery.data.presets.filter(
      (p) =>
        p.name.toLowerCase().includes(q) || (p.category && p.category.toLowerCase().includes(q)),
    );
  }, [detailQuery.data?.presets, presetSearch]);

  const filteredGmInstruments = useMemo(() => {
    if (!gmInstrumentsQuery.data) return [];
    if (!gmSearch.trim()) return gmInstrumentsQuery.data;
    const q = gmSearch.toLowerCase();
    return gmInstrumentsQuery.data.filter((g) => g.name.toLowerCase().includes(q));
  }, [gmInstrumentsQuery.data, gmSearch]);

  const importSf2 = useMutation({
    mutationFn: () =>
      instrumentsApi.importSoundFont(sf2File!, name.trim(), description.trim() || undefined),
    onSuccess: () => {
      setError(null);
      setActiveTab("list");
      setName("");
      setDescription("");
      setSf2File(null);
      queryClient.invalidateQueries({ queryKey: ["soundfonts"] });
    },
    onError: (err) => setError(err.message),
  });

  const importCsv = useMutation({
    mutationFn: () => instrumentsApi.importPresetTable(csvFile!, name.trim()),
    onSuccess: () => {
      setError(null);
      setActiveTab("list");
      setName("");
      setCsvFile(null);
      queryClient.invalidateQueries({ queryKey: ["soundfonts"] });
    },
    onError: (err) => setError(err.message),
  });

  const activateMutation = useMutation({
    mutationFn: (id: number) => instrumentsApi.activateSoundFont(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["soundfonts"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => instrumentsApi.deleteSoundFont(id),
    onSuccess: () => {
      setSelectedId(null);
      queryClient.invalidateQueries({ queryKey: ["soundfonts"] });
    },
  });

  const tabClass = (key: "list" | "sf2" | "csv" | "gm") =>
    `px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
      activeTab === key
        ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
        : "text-slate-600 hover:text-slate-900 hover:bg-slate-100 dark:text-slate-400 dark:hover:text-slate-100 dark:hover:bg-slate-800"
    }`;

  const canImportSf2 = name.trim().length > 0 && sf2File !== null;
  const canImportCsv = name.trim().length > 0 && csvFile !== null;

  return (
    <section className="space-y-6">
      <div className="rounded-lg border border-slate-200 bg-white p-6 space-y-4 dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold tracking-tight text-slate-900 dark:text-slate-100">
            {t("samples.soundfontTitle")}
          </h2>
          <div className="flex gap-1">
            <button type="button" className={tabClass("list")} onClick={() => setActiveTab("list")}>
              {t("samples.list")}
            </button>
            <button type="button" className={tabClass("gm")} onClick={() => setActiveTab("gm")}>
              {t("samples.gmList")}
            </button>
            <button type="button" className={tabClass("sf2")} onClick={() => setActiveTab("sf2")}>
              SF2
            </button>
            <button type="button" className={tabClass("csv")} onClick={() => setActiveTab("csv")}>
              CSV
            </button>
          </div>
        </div>

        <p className="text-xs text-slate-500 dark:text-slate-400">{t("samples.soundfontHint")}</p>

        {activeTab === "list" && (
          <div className="space-y-4">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={t("samples.searchSoundfont")}
                aria-label={t("samples.searchSoundfont")}
                className="flex-1 rounded border border-slate-300 bg-white px-3 py-1.5 text-sm focus:border-slate-500 focus:outline-none dark:border-slate-600 dark:bg-slate-700 dark:text-slate-100"
              />
              <select
                value={filterType}
                onChange={(e) => setFilterType(e.target.value as "all" | "sf2" | "preset_table")}
                aria-label={t("samples.filterAll")}
                className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm focus:border-slate-500 focus:outline-none dark:border-slate-600 dark:bg-slate-700 dark:text-slate-100"
              >
                <option value="all">{t("samples.filterAll")}</option>
                <option value="sf2">SF2</option>
                <option value="preset_table">CSV</option>
              </select>
            </div>
            {sfQuery.isLoading && (
              <p className="text-sm text-slate-500 dark:text-slate-400">{t("common.loading")}</p>
            )}
            {filteredSoundfonts.length === 0 && sfQuery.data && sfQuery.data.length > 0 && (
              <p className="text-sm text-slate-500 dark:text-slate-400">
                {t("samples.noMatchSoundfont")}
              </p>
            )}
            {sfQuery.data?.length === 0 && (
              <p className="text-sm text-slate-500 dark:text-slate-400">
                {t("samples.noSoundfonts")}
              </p>
            )}
            {filteredSoundfonts.length > 0 && (
              <div className="space-y-2">
                {filteredSoundfonts.map((sf) => (
                  <div
                    key={sf.id}
                    role="button"
                    tabIndex={0}
                    aria-label={sf.name}
                    className={`rounded border p-3 cursor-pointer transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 ${
                      selectedId === sf.id
                        ? "border-slate-400 bg-slate-50 dark:border-slate-600 dark:bg-slate-800"
                        : "border-slate-200 hover:border-slate-300 dark:border-slate-700 dark:hover:border-slate-600"
                    }`}
                    onClick={() => setSelectedId(selectedId === sf.id ? null : sf.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setSelectedId(selectedId === sf.id ? null : sf.id);
                      }
                    }}
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-slate-900 dark:text-slate-100">
                            {sf.name}
                          </span>
                          <span className="text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400">
                            {sf.type === "sf2" ? "SF2" : "CSV"}
                          </span>
                          {sf.is_active && (
                            <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400">
                              {t("samples.active")}
                            </span>
                          )}
                        </div>
                        <div className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                          {sf.preset_count} {t("samples.presets")} ·{" "}
                          {new Date(sf.created_at).toLocaleDateString()}
                        </div>
                      </div>
                      <div className="flex gap-2">
                        {!sf.is_active && (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              activateMutation.mutate(sf.id);
                            }}
                            className="text-xs px-3 py-1 rounded bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
                          >
                            {t("samples.activate")}
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            if (confirm(t("samples.deleteSoundfontConfirm", { name: sf.name }))) {
                              deleteMutation.mutate(sf.id);
                            }
                          }}
                          className="text-xs px-3 py-1 rounded bg-red-50 text-red-600 hover:bg-red-100 dark:bg-red-900/20 dark:text-red-400 dark:hover:bg-red-900/40"
                        >
                          {t("samples.delete")}
                        </button>
                      </div>
                    </div>
                    {selectedId === sf.id && detailQuery.data?.presets && (
                      <div className="mt-3 space-y-2">
                        <input
                          type="text"
                          value={presetSearch}
                          onChange={(e) => setPresetSearch(e.target.value)}
                          placeholder={t("samples.searchPreset")}
                          aria-label={t("samples.searchPreset")}
                          className="w-full rounded border border-slate-300 bg-white px-2 py-1 text-xs focus:border-slate-500 focus:outline-none dark:border-slate-600 dark:bg-slate-700 dark:text-slate-100"
                        />
                        <div className="max-h-64 overflow-y-auto rounded border border-slate-200 dark:border-slate-700">
                          <table className="w-full text-xs">
                            <thead className="sticky top-0 bg-slate-50 dark:bg-slate-800">
                              <tr className="text-left text-slate-600 dark:text-slate-300">
                                <th className="px-2 py-1 font-medium w-20">
                                  {t("samples.bankProg")}
                                </th>
                                <th className="px-2 py-1 font-medium">
                                  {t("samples.instrumentName")}
                                </th>
                                <th className="px-2 py-1 font-medium w-24">
                                  {t("samples.category")}
                                </th>
                              </tr>
                            </thead>
                            <tbody>
                              {filteredPresets.map((p, i) => (
                                <tr
                                  key={i}
                                  className="border-t border-slate-100 dark:border-slate-800"
                                >
                                  <td className="px-2 py-1 font-mono text-slate-500 dark:text-slate-400">
                                    {p.bank_msb}:{p.bank_lsb}/{p.program}
                                  </td>
                                  <td className="px-2 py-1 text-slate-700 dark:text-slate-200">
                                    {p.name}
                                  </td>
                                  <td className="px-2 py-1 text-slate-500 dark:text-slate-400">
                                    {p.category || "---"}
                                  </td>
                                </tr>
                              ))}
                              {filteredPresets.length === 0 && (
                                <tr>
                                  <td
                                    colSpan={3}
                                    className="px-2 py-3 text-center text-slate-500 dark:text-slate-400"
                                  >
                                    {t("samples.noMatchPreset")}
                                  </td>
                                </tr>
                              )}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {activeTab === "gm" && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium text-slate-700 dark:text-slate-200">
              {t("samples.gmInstrumentList")}
            </h3>
            <input
              type="text"
              value={gmSearch}
              onChange={(e) => setGmSearch(e.target.value)}
              placeholder={t("samples.searchGm")}
              aria-label={t("samples.searchGm")}
              className="w-full rounded border border-slate-300 bg-white px-3 py-1.5 text-sm focus:border-slate-500 focus:outline-none dark:border-slate-600 dark:bg-slate-700 dark:text-slate-100"
            />
            {gmInstrumentsQuery.isLoading && (
              <p className="text-sm text-slate-500 dark:text-slate-400">{t("common.loading")}</p>
            )}
            {gmInstrumentsQuery.data && (
              <div className="max-h-96 overflow-y-auto rounded border border-slate-200 dark:border-slate-700">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-slate-50 dark:bg-slate-800">
                    <tr className="text-left text-slate-600 dark:text-slate-300">
                      <th className="px-3 py-2 font-medium w-16">#</th>
                      <th className="px-3 py-2 font-medium">{t("samples.instrumentName")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredGmInstruments.map((inst) => (
                      <tr
                        key={inst.program}
                        className="border-t border-slate-100 dark:border-slate-800"
                      >
                        <td className="px-3 py-1.5 font-mono text-slate-500 dark:text-slate-400">
                          {inst.program}
                        </td>
                        <td className="px-3 py-1.5 text-slate-700 dark:text-slate-200">
                          {inst.name}
                        </td>
                      </tr>
                    ))}
                    {filteredGmInstruments.length === 0 && (
                      <tr>
                        <td
                          colSpan={2}
                          className="px-3 py-4 text-center text-slate-500 dark:text-slate-400"
                        >
                          {t("samples.noMatchGm")}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {activeTab === "sf2" && (
          <div className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <label className="block text-sm">
                <span className="font-medium text-slate-700 dark:text-slate-300">
                  {t("samples.name")}
                </span>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t("samples.soundfontNamePlaceholder")}
                  className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                />
              </label>
              <label className="block text-sm">
                <span className="font-medium text-slate-700 dark:text-slate-300">
                  {t("samples.description")}
                </span>
                <input
                  type="text"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder={t("samples.descriptionPlaceholder")}
                  className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                />
              </label>
            </div>

            <FilePicker
              label={t("samples.uploadSf2")}
              accept=".sf2,application/x-soundfont"
              multiple={false}
              files={sf2File ? [sf2File] : []}
              onFiles={(files) => setSf2File(files[0] ?? null)}
              inputRef={fileInputRef}
            />

            {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

            <div className="flex justify-end">
              <button
                type="button"
                onClick={() => importSf2.mutate()}
                disabled={!canImportSf2 || importSf2.isPending}
                className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
              >
                {importSf2.isPending ? t("samples.importing") : t("samples.import")}
              </button>
            </div>
          </div>
        )}

        {activeTab === "csv" && (
          <div className="space-y-4">
            <label className="block text-sm">
              <span className="font-medium text-slate-700 dark:text-slate-300">
                {t("samples.name")}
              </span>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("samples.presetTableNamePlaceholder")}
                className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
              />
            </label>

            <FilePicker
              label={t("samples.uploadCsv")}
              accept=".csv,text/csv"
              multiple={false}
              files={csvFile ? [csvFile] : []}
              onFiles={(files) => setCsvFile(files[0] ?? null)}
              inputRef={csvInputRef}
            />

            <div className="rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300">
              <p className="font-medium mb-1">{t("samples.csvFormat")}:</p>
              <code className="block whitespace-pre-wrap">
                bank_msb,bank_lsb,program,name,category,instrument_type
              </code>
            </div>

            {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

            <div className="flex justify-end">
              <button
                type="button"
                onClick={() => importCsv.mutate()}
                disabled={!canImportCsv || importCsv.isPending}
                className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
              >
                {importCsv.isPending ? t("samples.importing") : t("samples.import")}
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
