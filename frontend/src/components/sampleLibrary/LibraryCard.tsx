import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  instrumentsApi,
  type DrumTypeInfo,
  type SampleFileInfo,
  type SampleLibraryInfo,
} from "@/api/instruments";
import { api } from "@/api/axios";
import { getSharedAudioContext } from "@/utils/audioContext";
import { GM_DRUM_LABELS, QUERY_KEY } from "./constants";

interface LibraryCardProps {
  library: SampleLibraryInfo;
  isActive: boolean;
  onActivate: () => void;
  onDeactivate: () => void;
  onDelete: () => void;
  onUpdated: () => void;
  drumTypes: DrumTypeInfo[];
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
// missing any of these will still play --- those hits just use the default
// GM sound --- but the warning helps users know what's missing.
const CORE_DRUMS: number[] = [36, 38, 42, 46, 49, 51];
function findMissingNotes(files: SampleFileInfo[]): number[] {
  const present = new Set(files.map((f) => f.midi_note));
  return CORE_DRUMS.filter((note) => !present.has(note));
}

export function LibraryCard({
  library,
  isActive,
  onActivate,
  onDeactivate,
  onDelete,
  onUpdated,
  drumTypes,
}: LibraryCardProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [editingLibrary, setEditingLibrary] = useState(false);
  const [editName, setEditName] = useState(library.name);
  const [editDesc, setEditDesc] = useState(library.description ?? "");
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const bufferCacheRef = useRef<Map<number, AudioBuffer>>(new Map());
  const grouped = useMemo(() => groupByNote(library.files), [library.files]);
  const missing = useMemo(() => findMissingNotes(library.files), [library.files]);

  // Clear the decoded-sample cache on unmount so we don't leak an audio
  // graph per library card. The AudioContext itself is the shared singleton
  // (getSharedAudioContext) and stays alive for the whole page.
  useEffect(() => {
    const cache = bufferCacheRef.current;
    return () => {
      cache.clear();
    };
  }, []);

  const playSample = useCallback(
    async (note: number) => {
      try {
        const ctx = getSharedAudioContext();
        const cached = bufferCacheRef.current.get(note);
        if (cached) {
          const src = ctx.createBufferSource();
          src.buffer = cached;
          const gain = ctx.createGain();
          gain.gain.value = 0.5;
          src.connect(gain);
          gain.connect(ctx.destination);
          src.start();
          return;
        }
        // Use the authenticated axios instance (same path as the drum
        // player) instead of a bare fetch() so the request gets the
        // Bearer token / 401-refresh handling. The endpoint itself is
        // public, but one code path keeps behaviour consistent.
        const url = instrumentsApi.sampleUrl(library.id, note);
        const resp = await api.get<ArrayBuffer>(url, { responseType: "arraybuffer" });
        const audioBuffer = await ctx.decodeAudioData(resp.data);
        bufferCacheRef.current.set(note, audioBuffer);
        const src = ctx.createBufferSource();
        src.buffer = audioBuffer;
        const gain = ctx.createGain();
        gain.gain.value = 0.5;
        src.connect(gain);
        gain.connect(ctx.destination);
        src.start();
      } catch (err) {
        console.error("Failed to play sample", err);
      }
    },
    [library.id],
  );

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

  const batchRemove = useMutation({
    mutationFn: (ids: number[]) => instrumentsApi.batchRemoveSamples(library.id, ids),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      onUpdated();
      setSelectedIds(new Set());
    },
  });

  const updateLibrary = useMutation({
    mutationFn: (params: { name?: string; description?: string }) =>
      instrumentsApi.update(library.id, params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      onUpdated();
      setEditingLibrary(false);
    },
  });

  const handleNoteChange = (sampleId: number, newNote: number) => {
    updateSample.mutate({ sampleId, midi_note: newNote });
  };

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const selectAll = () => {
    setSelectedIds(new Set(library.files.filter((f) => f.id !== undefined).map((f) => f.id!)));
  };

  const clearSelection = () => {
    setSelectedIds(new Set());
  };

  const handleBatchDelete = () => {
    if (selectedIds.size === 0) return;
    if (!confirm(t("samples.batchDeleteConfirm", { count: selectedIds.size }))) return;
    batchRemove.mutate(Array.from(selectedIds));
  };

  const handleSaveLibrary = () => {
    if (!editName.trim()) return;
    updateLibrary.mutate({
      name: editName.trim(),
      description: editDesc.trim() || undefined,
    });
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
        <div className="min-w-0 flex-1">
          {editingLibrary ? (
            <div className="space-y-2">
              <input
                type="text"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                aria-label={t("samples.name")}
                className="w-full rounded border border-slate-300 bg-white px-2 py-1 text-base font-semibold focus:border-slate-500 focus:outline-none dark:border-slate-600 dark:bg-slate-700 dark:text-slate-100"
              />
              <input
                type="text"
                value={editDesc}
                onChange={(e) => setEditDesc(e.target.value)}
                placeholder={t("samples.descriptionPlaceholder")}
                aria-label={t("samples.description")}
                className="w-full rounded border border-slate-300 bg-white px-2 py-1 text-sm focus:border-slate-500 focus:outline-none dark:border-slate-600 dark:bg-slate-700 dark:text-slate-100"
              />
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={handleSaveLibrary}
                  disabled={updateLibrary.isPending || !editName.trim()}
                  className="rounded-md bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                >
                  {t("common.save")}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setEditName(library.name);
                    setEditDesc(library.description ?? "");
                    setEditingLibrary(false);
                  }}
                  className="rounded-md border border-slate-300 bg-white px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                >
                  {t("common.cancel")}
                </button>
              </div>
            </div>
          ) : (
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
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
                  <p className="text-sm text-slate-500 dark:text-slate-400">
                    {library.description}
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={() => setEditingLibrary(true)}
                className="shrink-0 rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
                title={t("samples.editLibrary")}
              >
                ✎
              </button>
            </div>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          {library.files.length > 0 && (
            <button
              type="button"
              onClick={() => {
                setEditing(!editing);
                if (editing) {
                  setSelectedIds(new Set());
                }
              }}
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
          {isActive && (
            <button
              type="button"
              onClick={onDeactivate}
              className="rounded-md border border-amber-300 bg-amber-50 px-3 py-1.5 text-sm font-medium text-amber-700 hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300 dark:hover:bg-amber-900/60"
            >
              {t("samples.deactivate")}
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
              notes: missing.slice(0, 5).join(", ") + (missing.length > 5 ? "..." : ""),
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
            <div className="mt-2 space-y-2 text-xs">
              <div className="flex items-center justify-between">
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={selectAll}
                    className="rounded border border-slate-300 bg-white px-2 py-1 text-[11px] text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
                  >
                    {t("samples.selectAll")}
                  </button>
                  <button
                    type="button"
                    onClick={clearSelection}
                    className="rounded border border-slate-300 bg-white px-2 py-1 text-[11px] text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
                  >
                    {t("samples.clearSelection")}
                  </button>
                </div>
                {selectedIds.size > 0 && (
                  <button
                    type="button"
                    onClick={handleBatchDelete}
                    disabled={batchRemove.isPending}
                    className="rounded border border-rose-200 bg-rose-50 px-2 py-1 text-[11px] text-rose-700 hover:bg-rose-100 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-900/60"
                  >
                    {t("samples.batchDelete")} ({selectedIds.size})
                  </button>
                )}
              </div>
              <ul className="mt-2 space-y-2">
                {library.files.map((sample) => (
                  <li
                    key={sample.id ?? sample.relative_path}
                    className="flex items-center gap-2 rounded border border-slate-200 bg-slate-50 p-2 dark:border-slate-700 dark:bg-slate-800/60"
                  >
                    <input
                      type="checkbox"
                      checked={sample.id !== undefined && selectedIds.has(sample.id)}
                      onChange={() => sample.id !== undefined && toggleSelect(sample.id)}
                      aria-label={sample.label}
                      className="h-3 w-3"
                    />
                    <button
                      type="button"
                      onClick={() => playSample(sample.midi_note)}
                      className="shrink-0 rounded border border-slate-300 bg-white px-2 py-1 text-[11px] text-slate-700 hover:bg-slate-100 dark:border-slate-600 dark:bg-slate-700 dark:text-slate-200 dark:hover:bg-slate-600"
                      title={t("samples.preview")}
                      aria-label={t("samples.preview")}
                    >
                      ▶
                    </button>
                    <select
                      value={sample.midi_note}
                      onChange={(e) => handleNoteChange(sample.id!, Number(e.target.value))}
                      disabled={updateSample.isPending}
                      aria-label={sample.label}
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
            </div>
          ) : (
            <ul className="mt-2 grid grid-cols-1 gap-1 text-xs sm:grid-cols-2 md:grid-cols-3">
              {[...grouped.entries()].map(([note, files]) => (
                <li
                  key={note}
                  className="flex items-center justify-between gap-2 rounded border border-slate-100 bg-slate-50 px-2 py-1 dark:border-slate-800 dark:bg-slate-800/60"
                >
                  <button
                    type="button"
                    onClick={() => playSample(note)}
                    className="shrink-0 text-slate-500 hover:text-emerald-600 dark:text-slate-400 dark:hover:text-emerald-400"
                    title={t("samples.preview")}
                  >
                    ▶
                  </button>
                  <span className="font-mono text-slate-500 dark:text-slate-400">{note}</span>
                  <span className="flex-1 truncate text-slate-700 dark:text-slate-200">
                    {GM_DRUM_LABELS[note] ?? t("samples.noteFallback", { note })}
                  </span>
                  <span className="text-slate-400 dark:text-slate-500">
                    {files.length > 1 ? `x${files.length}` : ""}
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
