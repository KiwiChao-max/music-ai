import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { useUploadAudio } from "@/hooks/useAudioTasks";
import { useFileDrop } from "@/hooks/useFileDrop";
import { MAX_UPLOAD_BYTES, looksLikeAudio, validateAudioFile } from "@/utils/upload";
import { ErrorState } from "@/components/States";

const ACCEPT = "audio/*";

export function UploadPage() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [picked, setPicked] = useState<File | null>(null);
  const [dropError, setDropError] = useState<string | null>(null);
  const upload = useUploadAudio();
  const navigate = useNavigate();
  const { t } = useTranslation();

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null;
    setPicked(f);
    setDropError(null);
  };

  const acceptFile = (file: File | null) => {
    if (!file) return;
    if (!looksLikeAudio(file)) {
      setDropError(t("upload.invalidType", { defaultValue: "Please drop an audio file." }));
      setPicked(null);
      return;
    }
    setDropError(null);
    setPicked(file);
  };

  const { isDragging, onDragOver, onDragEnter, onDragLeave, onDrop } = useFileDrop({
    multiple: false,
    onFiles: (files) => acceptFile(files[0] ?? null),
  });

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!picked || upload.isPending) return;
    upload.mutate(picked, {
      onSuccess: (resp) => navigate(`/audio/${resp.task_id}`),
    });
  };

  const onReset = () => {
    setPicked(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const sizeKB = picked ? (picked.size / 1024).toFixed(1) : null;
  const validationError = validateAudioFile(picked);
  const maxMB = Math.floor(MAX_UPLOAD_BYTES / (1024 * 1024));

  return (
    <section className="max-w-2xl space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
          {t("upload.title")}
        </h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">{t("upload.subtitle")}</p>
      </header>

      <form
        onSubmit={onSubmit}
        className="space-y-4 rounded-lg border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900"
      >
        <label
          htmlFor="file"
          onDragOver={onDragOver}
          onDragEnter={onDragEnter}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          className={`flex cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed px-6 py-10 text-center transition-colors ${
            isDragging
              ? "border-indigo-500 bg-indigo-50 dark:border-indigo-400 dark:bg-indigo-950/40"
              : "border-slate-300 bg-slate-50 hover:border-slate-400 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-800 dark:hover:border-slate-500 dark:hover:bg-slate-700"
          }`}
        >
          <span className="text-sm font-medium text-slate-700 dark:text-slate-200">
            {t("upload.dropLabel")}
          </span>
          <span className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            {t("upload.dropHelp", { max: maxMB })}
          </span>
          <input
            id="file"
            ref={fileInputRef}
            type="file"
            accept={ACCEPT}
            onChange={onPick}
            className="sr-only"
          />
        </label>

        {picked && (
          <div className="flex items-center justify-between rounded-md border border-slate-200 bg-slate-50 px-4 py-3 dark:border-slate-700 dark:bg-slate-800">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">
                {picked.name}
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {picked.type || t("upload.unknownType")} · {sizeKB} KB
              </p>
            </div>
            <button
              type="button"
              onClick={onReset}
              className="text-sm text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
            >
              {t("upload.remove")}
            </button>
          </div>
        )}

        {upload.isError && (
          <ErrorState
            title={t("upload.error.title")}
            error={upload.error}
            onRetry={() => upload.mutate(picked!)}
          />
        )}
        {dropError && <p className="text-sm text-red-600 dark:text-red-400">{dropError}</p>}
        {validationError && (
          <p className="text-sm text-red-600 dark:text-red-400">{validationError}</p>
        )}

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onReset}
            disabled={!picked || upload.isPending}
            className="rounded-md px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-900 disabled:opacity-50 dark:text-slate-300 dark:hover:text-slate-100"
          >
            {t("upload.clear")}
          </button>
          <button
            type="submit"
            disabled={!picked || Boolean(validationError) || upload.isPending}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
          >
            {upload.isPending ? t("upload.submitting") : t("upload.submit")}
          </button>
        </div>
      </form>
    </section>
  );
}
