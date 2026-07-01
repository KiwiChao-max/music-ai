import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  instrumentsApi,
  type DrumTypeInfo,
  type SampleClassification,
  type SampleFileInfo,
  type SampleLibraryInfo,
} from "@/api/instruments";

// Friendly label for each GM percussion note (35..81). Used to display the
// library contents in a way drummers actually read.
const GM_DRUM_LABELS: Record<number, string> = {
  35: "Acoustic Bass Drum",
  36: "Bass Drum 1",
  37: "Side Stick",
  38: "Acoustic Snare",
  39: "Hand Clap",
  40: "Electric Snare",
  41: "Low Floor Tom",
  42: "Closed Hi-Hat",
  43: "High Floor Tom",
  44: "Pedal Hi-Hat",
  45: "Low Tom",
  46: "Open Hi-Hat",
  47: "Low-Mid Tom",
  48: "Hi-Mid Tom",
  49: "Crash Cymbal 1",
  50: "High Tom",
  51: "Ride Cymbal 1",
  52: "Chinese Cymbal",
  53: "Ride Bell",
  54: "Tambourine",
  55: "Splash Cymbal",
  56: "Cowbell",
  57: "Crash Cymbal 2",
  58: "Vibraslap",
  59: "Ride Cymbal 2",
  60: "High Bongo",
  61: "Low Bongo",
  62: "Mute High Conga",
  63: "Open High Conga",
  64: "Low Conga",
  65: "High Timbale",
  66: "Low Timbale",
  67: "High Agogo",
  68: "Low Agogo",
  69: "Cabasa",
  70: "Maracas",
  71: "Short Whistle",
  72: "Long Whistle",
  73: "Short Guiro",
  74: "Long Guiro",
  75: "Claves",
  76: "High Wood Block",
  77: "Low Wood Block",
  78: "Mute Cuica",
  79: "Open Cuica",
  80: "Mute Triangle",
  81: "Open Triangle",
};

const QUERY_KEY = ["sample-libraries"] as const;

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
        <p className="text-sm text-slate-600 dark:text-slate-400">
          {t("samples.subtitle")}
        </p>
      </header>

      <div className="border-b border-slate-200 dark:border-slate-800">
        <nav className="flex gap-1">
          <button type="button" className={tabClass("samples")} onClick={() => setActiveTab("samples")}>
            {t("samples.tabDrumKits")}
          </button>
          <button type="button" className={tabClass("soundfonts")} onClick={() => setActiveTab("soundfonts")}>
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

      {activeTab === "soundfonts" && <SoundFontPanel />}
    </section>
  );
}

function UploadCard() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const zipInputRef = useRef<HTMLInputElement | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [pickedFiles, setPickedFiles] = useState<File[]>([]);
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [classifications, setClassifications] = useState<Map<File, SampleClassification>>(new Map());

  const classifyFiles = useMutation({
    mutationFn: async (files: File[]) => {
      const results: Map<File, SampleClassification> = new Map();
      for (const file of files) {
        try {
          const result = await instrumentsApi.classify(file);
          results.set(file, result);
        } catch {
          continue;
        }
      }
      return results;
    },
    onSuccess: (results) => {
      const newClassifications = new Map(classifications);
      for (const [file, result] of results) {
        newClassifications.set(file, result);
      }
      setClassifications(newClassifications);
    },
  });

  const handleFilesChange = (files: File[]) => {
    setPickedFiles(files);
    if (files.length > 0) {
      classifyFiles.mutate(files);
    }
  };

  const create = useMutation({
    mutationFn: () =>
      instrumentsApi.create({
        name: name.trim(),
        description: description.trim() || undefined,
        files: pickedFiles,
        zipFile: zipFile ?? undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      setName("");
      setDescription("");
      setPickedFiles([]);
      setZipFile(null);
      setClassifications(new Map());
      if (fileInputRef.current) fileInputRef.current.value = "";
      if (zipInputRef.current) zipInputRef.current.value = "";
      setError(null);
    },
    onError: (err) => setError(err.message),
  });

  const canSubmit = name.trim().length > 0 && (pickedFiles.length > 0 || zipFile !== null);

  const getConfidenceColor = (confidence: number) => {
    if (confidence >= 0.8) return "text-emerald-600 dark:text-emerald-400";
    if (confidence >= 0.6) return "text-amber-600 dark:text-amber-400";
    return "text-rose-600 dark:text-rose-400";
  };

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6 space-y-4 dark:border-slate-800 dark:bg-slate-900">
      <h2 className="text-lg font-semibold tracking-tight text-slate-900 dark:text-slate-100">
        {t("samples.new")}
      </h2>
      <p className="text-xs text-slate-500 dark:text-slate-400">
        {t("samples.autoDetectHint")}
      </p>
      <div className="grid gap-4 md:grid-cols-2">
        <label className="block text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-300">{t("samples.name")}</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("samples.namePlaceholder")}
            className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
          />
        </label>
        <label className="block text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-300">{t("samples.description")}</span>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={t("samples.descriptionPlaceholder")}
            className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
          />
        </label>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <FilePicker
          label={t("samples.files")}
          accept="audio/*"
          multiple
          files={pickedFiles}
          onFiles={handleFilesChange}
          inputRef={fileInputRef}
        />
        <FilePicker
          label={t("samples.orZip")}
          accept=".zip,application/zip"
          multiple={false}
          files={zipFile ? [zipFile] : []}
          onFiles={(files) => setZipFile(files[0] ?? null)}
          inputRef={zipInputRef}
        />
      </div>

      {pickedFiles.length > 0 && classifyFiles.isPending && (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {t("samples.classifying")}
        </p>
      )}

      {pickedFiles.length > 0 && !classifyFiles.isPending && (
        <section className="rounded border border-slate-200 bg-slate-50 p-4 dark:border-slate-700 dark:bg-slate-800">
          <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-2">
            {t("samples.classificationPreview")}
          </h3>
          <ul className="space-y-2 text-xs">
            {pickedFiles.map((file, index) => {
              const classification = classifications.get(file);
              return (
                <li
                  key={index}
                  className="flex items-center justify-between gap-4 rounded bg-white px-3 py-2 dark:bg-slate-700"
                >
                  <span className="truncate text-slate-700 dark:text-slate-200">
                    {file.name}
                  </span>
                  {classification ? (
                    <>
                      <span className="font-medium text-slate-900 dark:text-slate-100">
                        {classification.drum_type_label}
                      </span>
                      <span className="font-mono text-slate-500 dark:text-slate-400">
                        #{classification.midi_note}
                      </span>
                      <span className={`font-mono ${getConfidenceColor(classification.confidence)}`}>
                        {(classification.confidence * 100).toFixed(0)}%
                      </span>
                    </>
                  ) : (
                    <span className="text-slate-400 dark:text-slate-500">
                      {t("samples.classificationPending")}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => create.mutate()}
          disabled={!canSubmit || create.isPending}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
        >
          {create.isPending ? t("samples.creating") : t("samples.create")}
        </button>
      </div>
    </section>
  );
}

interface FilePickerProps {
  label: string;
  accept: string;
  multiple: boolean;
  files: File[];
  onFiles: (files: File[]) => void;
  inputRef: React.RefObject<HTMLInputElement | null>;
}

function FilePicker({ label, accept, multiple, files, onFiles, inputRef }: FilePickerProps) {
  const { t } = useTranslation();
  return (
    <label className="flex cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center transition-colors hover:border-slate-400 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-800 dark:hover:border-slate-500 dark:hover:bg-slate-700">
      <span className="text-sm font-medium text-slate-700 dark:text-slate-200">{label}</span>
      {files.length > 0 && (
        <span className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          {files.length === 1
            ? files[0].name
            : t("samples.filesSelectedPlural", { count: files.length })}
        </span>
      )}
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        onChange={(e) => onFiles(Array.from(e.target.files ?? []))}
        className="sr-only"
      />
    </label>
  );
}

interface LibraryCardProps {
  library: SampleLibraryInfo;
  isActive: boolean;
  onActivate: () => void;
  onDelete: () => void;
  onUpdated: () => void;
  drumTypes: DrumTypeInfo[];
}

function LibraryCard({ library, isActive, onActivate, onDelete, onUpdated, drumTypes }: LibraryCardProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const grouped = useMemo(() => groupByNote(library.files), [library.files]);
  const missing = useMemo(() => findMissingNotes(library.files), [library.files]);

  const updateSample = useMutation({
    mutationFn: (params: { sampleId: number; midi_note?: number; label?: string }) =>
      instrumentsApi.updateSample(library.id, params.sampleId, {
        midi_note: params.midi_note,
        label: params.label,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      onUpdated();
    },
  });

  const removeSample = useMutation({
    mutationFn: (sampleId: number) => instrumentsApi.removeSample(library.id, sampleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      onUpdated();
    },
  });

  const handleNoteChange = (sampleId: number, newNote: number) => {
    updateSample.mutate({ sampleId, midi_note: newNote });
  };

  const drumNoteOptions = useMemo(() => {
    return drumTypes.length > 0
      ? drumTypes.map((d) => ({ value: d.midi_note, label: `${d.midi_note} - ${d.label}` }))
      : Object.entries(GM_DRUM_LABELS).map(([note, label]) => ({
          value: Number(note),
          label: `${note} - ${label}`,
        }));
  }, [drumTypes]);

  return (
    <li
      className={`rounded-lg border bg-white p-5 space-y-3 dark:bg-slate-900 ${
        isActive
          ? "border-emerald-400 ring-2 ring-emerald-100 dark:border-emerald-500 dark:ring-emerald-900/40"
          : "border-slate-200 dark:border-slate-800"
      }`}
    >
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-base font-semibold text-slate-900 dark:text-slate-100">
              {library.name}
            </h3>
            {isActive && (
              <span className="inline-flex items-center rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">
                {t("samples.active")}
              </span>
            )}
          </div>
          {library.description && (
            <p className="text-sm text-slate-500 dark:text-slate-400">{library.description}</p>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          {library.files.length > 0 && (
            <button
              type="button"
              onClick={() => setEditing(!editing)}
              className={`rounded-md border px-3 py-1.5 text-sm font-medium ${
                editing
                  ? "border-slate-500 bg-slate-200 text-slate-900 dark:border-slate-600 dark:bg-slate-700 dark:text-slate-100"
                  : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
              }`}
            >
              {editing ? t("common.cancel") : t("samples.edit")}
            </button>
          )}
          {!isActive && (
            <button
              type="button"
              onClick={onActivate}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
            >
              {t("samples.activate")}
            </button>
          )}
          <button
            type="button"
            onClick={onDelete}
            className="rounded-md border border-rose-200 bg-rose-50 px-3 py-1.5 text-sm font-medium text-rose-700 hover:bg-rose-100 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-900/60"
          >
            {t("samples.delete")}
          </button>
          <a
            href={instrumentsApi.exportLibrary(library.id)}
            download
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
          >
            {t("samples.export")}
          </a>
        </div>
      </header>

      <div className="text-xs text-slate-500 dark:text-slate-400">
        {t(library.files.length === 1 ? "samples.stats" : "samples.statsPlural", {
          count: library.files.length,
          notes: grouped.size,
        })}
        {missing.length > 0 && (
          <span className="ml-1 text-amber-600 dark:text-amber-400">
            ·{" "}
            {t(missing.length === 1 ? "samples.missing" : "samples.missingPlural", {
              count: missing.length,
              notes: missing.slice(0, 5).join(", ") + (missing.length > 5 ? "…" : ""),
            })}
          </span>
        )}
      </div>

      {library.files.length > 0 && (
        <details className="text-sm" open={editing}>
          <summary className="cursor-pointer text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-slate-100">
            {t("samples.showAll")}
          </summary>
          {editing ? (
            <ul className="mt-2 space-y-2 text-xs">
              {library.files.map((sample) => (
                <li
                  key={sample.id ?? sample.relative_path}
                  className="flex items-center gap-2 rounded border border-slate-200 bg-slate-50 p-2 dark:border-slate-700 dark:bg-slate-800/60"
                >
                  <select
                    value={sample.midi_note}
                    onChange={(e) => handleNoteChange(sample.id!, Number(e.target.value))}
                    disabled={updateSample.isPending}
                    className="flex-1 rounded border border-slate-300 bg-white px-2 py-1 text-xs focus:border-slate-500 focus:outline-none dark:border-slate-600 dark:bg-slate-700 dark:text-slate-100"
                  >
                    {drumNoteOptions.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                  <span className="w-24 truncate text-slate-600 dark:text-slate-300">
                    {sample.label}
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      if (confirm(t("samples.deleteSampleConfirm", { name: sample.label }))) {
                        removeSample.mutate(sample.id!);
                      }
                    }}
                    disabled={removeSample.isPending}
                    className="shrink-0 rounded border border-rose-200 bg-rose-50 px-2 py-1 text-[11px] text-rose-700 hover:bg-rose-100 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-900/60"
                  >
                    {t("samples.deleteSample")}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <ul className="mt-2 grid grid-cols-1 gap-1 text-xs sm:grid-cols-2 md:grid-cols-3">
              {[...grouped.entries()].map(([note, files]) => (
                <li
                  key={note}
                  className="flex items-center justify-between rounded border border-slate-100 bg-slate-50 px-2 py-1 dark:border-slate-800 dark:bg-slate-800/60"
                >
                  <span className="font-mono text-slate-500 dark:text-slate-400">{note}</span>
                  <span className="truncate text-slate-700 dark:text-slate-200">
                    {GM_DRUM_LABELS[note] ?? t("samples.noteFallback", { note })}
                  </span>
                  <span className="text-slate-400 dark:text-slate-500">
                    {files.length > 1 ? `×${files.length}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </details>
      )}
    </li>
  );
}

function groupByNote(files: SampleFileInfo[]): Map<number, SampleFileInfo[]> {
  const out = new Map<number, SampleFileInfo[]>();
  for (const file of files) {
    const bucket = out.get(file.midi_note) ?? [];
    bucket.push(file);
    out.set(file.midi_note, bucket);
  }
  return out;
}

// Recommend a small set of "core" notes for a usable drum kit. A library
// missing any of these will still play — those hits just use the default
// GM sound — but the warning helps users know what's missing.
const CORE_DRUMS: number[] = [36, 38, 42, 46, 49, 51];
function findMissingNotes(files: SampleFileInfo[]): number[] {
  const present = new Set(files.map((f) => f.midi_note));
  return CORE_DRUMS.filter((note) => !present.has(note));
}

function SoundFontPanel() {
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

  const sfQuery = useQuery({
    queryKey: ["soundfonts"],
    queryFn: instrumentsApi.listSoundFonts,
  });

  const detailQuery = useQuery({
    queryKey: ["soundfonts", selectedId],
    queryFn: () => instrumentsApi.getSoundFont(selectedId!),
    enabled: selectedId !== null,
  });

  const gmInstrumentsQuery = useQuery({
    queryKey: ["gm-instruments"],
    queryFn: instrumentsApi.listGmInstruments,
  });

  const importSf2 = useMutation({
    mutationFn: () => instrumentsApi.importSoundFont(sf2File!, name.trim(), description.trim() || undefined),
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

        <p className="text-xs text-slate-500 dark:text-slate-400">
          {t("samples.soundfontHint")}
        </p>

        {activeTab === "list" && (
          <div className="space-y-4">
            {sfQuery.isLoading && (
              <p className="text-sm text-slate-500 dark:text-slate-400">{t("common.loading")}</p>
            )}
            {sfQuery.data?.length === 0 && (
              <p className="text-sm text-slate-500 dark:text-slate-400">
                {t("samples.noSoundfonts")}
              </p>
            )}
            {sfQuery.data && sfQuery.data.length > 0 && (
              <div className="space-y-2">
                {sfQuery.data.map((sf) => (
                  <div
                    key={sf.id}
                    className={`rounded border p-3 cursor-pointer transition-colors ${
                      selectedId === sf.id
                        ? "border-slate-400 bg-slate-50 dark:border-slate-600 dark:bg-slate-800"
                        : "border-slate-200 hover:border-slate-300 dark:border-slate-700 dark:hover:border-slate-600"
                    }`}
                    onClick={() => setSelectedId(selectedId === sf.id ? null : sf.id)}
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
                          {sf.preset_count} {t("samples.presets")} · {new Date(sf.created_at).toLocaleDateString()}
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
                      <div className="mt-3 max-h-64 overflow-y-auto rounded border border-slate-200 dark:border-slate-700">
                        <table className="w-full text-xs">
                          <thead className="sticky top-0 bg-slate-50 dark:bg-slate-800">
                            <tr className="text-left text-slate-600 dark:text-slate-300">
                              <th className="px-2 py-1 font-medium w-20">Bank:Prog</th>
                              <th className="px-2 py-1 font-medium">{t("samples.instrumentName")}</th>
                              <th className="px-2 py-1 font-medium w-24">{t("samples.category")}</th>
                            </tr>
                          </thead>
                          <tbody>
                            {detailQuery.data.presets.map((p, i) => (
                              <tr key={i} className="border-t border-slate-100 dark:border-slate-800">
                                <td className="px-2 py-1 font-mono text-slate-500 dark:text-slate-400">
                                  {p.bank_msb}:{p.bank_lsb}/{p.program}
                                </td>
                                <td className="px-2 py-1 text-slate-700 dark:text-slate-200">{p.name}</td>
                                <td className="px-2 py-1 text-slate-500 dark:text-slate-400">
                                  {p.category || "—"}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
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
                    {gmInstrumentsQuery.data.map((inst) => (
                      <tr key={inst.program} className="border-t border-slate-100 dark:border-slate-800">
                        <td className="px-3 py-1.5 font-mono text-slate-500 dark:text-slate-400">
                          {inst.program}
                        </td>
                        <td className="px-3 py-1.5 text-slate-700 dark:text-slate-200">
                          {inst.name}
                        </td>
                      </tr>
                    ))}
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
                <span className="font-medium text-slate-700 dark:text-slate-300">{t("samples.name")}</span>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t("samples.soundfontNamePlaceholder")}
                  className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                />
              </label>
              <label className="block text-sm">
                <span className="font-medium text-slate-700 dark:text-slate-300">{t("samples.description")}</span>
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
              <span className="font-medium text-slate-700 dark:text-slate-300">{t("samples.name")}</span>
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
