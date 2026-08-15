import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { instrumentsApi, type SampleClassification } from "@/api/instruments";
import { FilePicker } from "./FilePicker";
import { QUERY_KEY } from "./constants";

interface ClassificationOutcome {
  results: Map<File, SampleClassification>;
  failed: number;
}

export function UploadCard() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const zipInputRef = useRef<HTMLInputElement | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [pickedFiles, setPickedFiles] = useState<File[]>([]);
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [classificationHint, setClassificationHint] = useState<string | null>(null);
  const [classifications, setClassifications] = useState<Map<File, SampleClassification>>(
    new Map(),
  );

  // Classify every picked file in parallel. The old loop was one HTTP
  // round-trip per file and swallowed failures silently; allSettled keeps
  // the successes AND reports how many files failed.
  const classifyFiles = useMutation({
    mutationFn: async (files: File[]): Promise<ClassificationOutcome> => {
      const settled = await Promise.allSettled(files.map((file) => instrumentsApi.classify(file)));
      const results = new Map<File, SampleClassification>();
      let failed = 0;
      settled.forEach((outcome, index) => {
        if (outcome.status === "fulfilled") {
          results.set(files[index], outcome.value);
        } else {
          failed++;
        }
      });
      return { results, failed };
    },
  });

  const handleFilesChange = (files: File[]) => {
    setError(null);
    setClassificationHint(null);
    setPickedFiles(files);
    if (files.length > 0) {
      classifyFiles.mutate(files, {
        onSuccess: ({ results, failed }) => {
          setClassifications(results);
          if (failed > 0) {
            setClassificationHint(
              t(failed === 1 ? "samples.classifyFailure" : "samples.classifyFailurePlural", {
                count: failed,
              }),
            );
          }
        },
      });
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
      setClassificationHint(null);
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
      <p className="text-xs text-slate-500 dark:text-slate-400">{t("samples.autoDetectHint")}</p>
      <div className="grid gap-4 md:grid-cols-2">
        <label className="block text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-300">
            {t("samples.name")}
          </span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("samples.namePlaceholder")}
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
          onFiles={(files) => {
            setError(null);
            setZipFile(files[0] ?? null);
          }}
          inputRef={zipInputRef}
        />
      </div>

      {pickedFiles.length > 0 && classifyFiles.isPending && (
        <p className="text-xs text-slate-500 dark:text-slate-400">{t("samples.classifying")}</p>
      )}

      {classificationHint && (
        <p className="text-xs text-amber-700 dark:text-amber-300">{classificationHint}</p>
      )}

      {pickedFiles.length > 0 && !classifyFiles.isPending && (
        <section className="rounded border border-slate-200 bg-slate-50 p-4 dark:border-slate-700 dark:bg-slate-800">
          <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-2">
            {t("samples.classificationPreview")}
          </h3>
          <div className="flex items-center gap-4 px-3 py-1 text-[11px] font-medium text-slate-500 dark:text-slate-400">
            <span className="flex-1">{t("samples.colFile")}</span>
            <span className="w-32">{t("samples.colType")}</span>
            <span className="w-12 text-right">{t("samples.colNote")}</span>
            <span className="w-12 text-right">{t("samples.colConfidence")}</span>
          </div>
          <ul className="space-y-2 text-xs">
            {pickedFiles.map((file, index) => {
              const classification = classifications.get(file);
              return (
                <li
                  key={index}
                  className="flex items-center gap-4 rounded bg-white px-3 py-2 dark:bg-slate-700"
                >
                  <span className="truncate flex-1 text-slate-700 dark:text-slate-200">
                    {file.name}
                  </span>
                  {classification ? (
                    <>
                      <span className="w-32 truncate font-medium text-slate-900 dark:text-slate-100">
                        {classification.drum_type_label}
                      </span>
                      <span className="w-12 text-right font-mono text-slate-500 dark:text-slate-400">
                        #{classification.midi_note}
                      </span>
                      <span
                        className={`w-12 text-right font-mono ${getConfidenceColor(classification.confidence)}`}
                      >
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
