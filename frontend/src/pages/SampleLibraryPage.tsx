import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  instrumentsApi,
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

export function SampleLibraryPage() {
  const queryClient = useQueryClient();
  const librariesQuery = useQuery({
    queryKey: QUERY_KEY,
    queryFn: instrumentsApi.list,
  });
  const activeQuery = useQuery({
    queryKey: [...QUERY_KEY, "active"],
    queryFn: instrumentsApi.active,
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

  return (
    <section className="space-y-8">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">Sample libraries</h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Upload your own drum samples. The active library plays back
          generated drum MIDI in place of the default GM bank. Filenames
          like <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">kick.wav</code>,{" "}
          <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">snare.wav</code>,{" "}
          <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">open_hat.wav</code> are
          mapped to their GM percussion notes automatically.
        </p>
      </header>

      <UploadCard />

      <section className="space-y-3">
        <h2 className="text-lg font-semibold tracking-tight text-slate-900 dark:text-slate-100">Your libraries</h2>
        {librariesQuery.isLoading && (
          <p className="text-sm text-slate-500 dark:text-slate-400">Loading…</p>
        )}
        {librariesQuery.isError && (
          <p className="text-sm text-red-600 dark:text-red-400">
            Failed to load libraries: {librariesQuery.error.message}
          </p>
        )}
        {libraries.length === 0 && !librariesQuery.isLoading && (
          <p className="rounded-md border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
            No sample libraries yet. Upload one above to get started.
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
                if (confirm(`Delete library "${library.name}"?`)) {
                  remove.mutate(library.id);
                }
              }}
            />
          ))}
        </ul>
      </section>
    </section>
  );
}

function UploadCard() {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const zipInputRef = useRef<HTMLInputElement | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [pickedFiles, setPickedFiles] = useState<File[]>([]);
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);

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
      if (fileInputRef.current) fileInputRef.current.value = "";
      if (zipInputRef.current) zipInputRef.current.value = "";
      setError(null);
    },
    onError: (err) => setError(err.message),
  });

  const canSubmit = name.trim().length > 0 && (pickedFiles.length > 0 || zipFile !== null);

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6 space-y-4 dark:border-slate-800 dark:bg-slate-900">
      <h2 className="text-lg font-semibold tracking-tight text-slate-900 dark:text-slate-100">Upload a new library</h2>
      <div className="grid gap-4 md:grid-cols-2">
        <label className="block text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-300">Library name</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My studio kit"
            className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
          />
        </label>
        <label className="block text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-300">Description (optional)</span>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Recorded 2025-08-12"
            className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
          />
        </label>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <FilePicker
          label="Drop multiple WAV / AIFF / FLAC files"
          accept="audio/*"
          multiple
          files={pickedFiles}
          onFiles={setPickedFiles}
          inputRef={fileInputRef}
        />
        <FilePicker
          label="… or upload a single .zip archive"
          accept=".zip,application/zip"
          multiple={false}
          files={zipFile ? [zipFile] : []}
          onFiles={(files) => setZipFile(files[0] ?? null)}
          inputRef={zipInputRef}
        />
      </div>

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => create.mutate()}
          disabled={!canSubmit || create.isPending}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
        >
          {create.isPending ? "Uploading…" : "Create library"}
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
  return (
    <label className="flex cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center transition-colors hover:border-slate-400 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-800 dark:hover:border-slate-500 dark:hover:bg-slate-700">
      <span className="text-sm font-medium text-slate-700 dark:text-slate-200">{label}</span>
      {files.length > 0 && (
        <span className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          {files.length === 1 ? files[0].name : `${files.length} file(s) selected`}
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
}

function LibraryCard({ library, isActive, onActivate, onDelete }: LibraryCardProps) {
  const grouped = useMemo(() => groupByNote(library.files), [library.files]);
  const missing = useMemo(() => findMissingNotes(library.files), [library.files]);

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
                Active
              </span>
            )}
          </div>
          {library.description && (
            <p className="text-sm text-slate-500 dark:text-slate-400">{library.description}</p>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          {!isActive && (
            <button
              type="button"
              onClick={onActivate}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
            >
              Activate
            </button>
          )}
          <button
            type="button"
            onClick={onDelete}
            className="rounded-md border border-rose-200 bg-rose-50 px-3 py-1.5 text-sm font-medium text-rose-700 hover:bg-rose-100 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-900/60"
          >
            Delete
          </button>
        </div>
      </header>

      <div className="text-xs text-slate-500 dark:text-slate-400">
        {library.files.length} sample
        {library.files.length === 1 ? "" : "s"} mapped · {grouped.size} distinct GM notes
        {missing.length > 0 && (
          <span className="ml-1 text-amber-600 dark:text-amber-400">
            · missing {missing.length} note
            {missing.length === 1 ? "" : "s"}: {missing.slice(0, 5).join(", ")}
            {missing.length > 5 ? "…" : ""}
          </span>
        )}
      </div>

      {library.files.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-slate-100">
            Show all samples
          </summary>
          <ul className="mt-2 grid grid-cols-1 gap-1 text-xs sm:grid-cols-2 md:grid-cols-3">
            {[...grouped.entries()].map(([note, files]) => (
              <li
                key={note}
                className="flex items-center justify-between rounded border border-slate-100 bg-slate-50 px-2 py-1 dark:border-slate-800 dark:bg-slate-800/60"
              >
                <span className="font-mono text-slate-500 dark:text-slate-400">{note}</span>
                <span className="truncate text-slate-700 dark:text-slate-200">
                  {GM_DRUM_LABELS[note] ?? `Note ${note}`}
                </span>
                <span className="text-slate-400 dark:text-slate-500">
                  {files.length > 1 ? `×${files.length}` : ""}
                </span>
              </li>
            ))}
          </ul>
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
