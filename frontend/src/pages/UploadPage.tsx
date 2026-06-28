import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useUploadAudio } from "@/hooks/useAudioTasks";
import { MAX_UPLOAD_BYTES, validateAudioFile } from "@/utils/upload";
import { ErrorState } from "@/components/States";

const ACCEPT = "audio/*";

export function UploadPage() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [picked, setPicked] = useState<File | null>(null);
  const upload = useUploadAudio();
  const navigate = useNavigate();

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null;
    setPicked(f);
  };

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
        <h1 className="text-2xl font-bold tracking-tight">Upload audio</h1>
        <p className="text-sm text-slate-600">
          Pick an audio file to create a new task. You'll be taken to the task
          page as soon as the upload completes.
        </p>
      </header>

      <form
        onSubmit={onSubmit}
        className="space-y-4 rounded-lg border border-slate-200 bg-white p-6"
      >
        <label
          htmlFor="file"
          className="flex cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center transition-colors hover:border-slate-400 hover:bg-slate-100"
        >
          <span className="text-sm font-medium text-slate-700">
            Click to choose an audio file
          </span>
          <span className="mt-1 text-xs text-slate-500">
            Supports browser-readable audio files up to {maxMB} MB
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
          <div className="flex items-center justify-between rounded-md border border-slate-200 bg-slate-50 px-4 py-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-slate-900">
                {picked.name}
              </p>
              <p className="text-xs text-slate-500">
                {picked.type || "unknown type"} · {sizeKB} KB
              </p>
            </div>
            <button
              type="button"
              onClick={onReset}
              className="text-sm text-slate-500 hover:text-slate-900"
            >
              Remove
            </button>
          </div>
        )}

        {upload.isError && (
          <ErrorState
            title="Upload failed"
            error={upload.error}
            onRetry={() => upload.mutate(picked!)}
          />
        )}
        {validationError && (
          <p className="text-sm text-red-600">{validationError}</p>
        )}

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onReset}
            disabled={!picked || upload.isPending}
            className="rounded-md px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-900 disabled:opacity-50"
          >
            Clear
          </button>
          <button
            type="submit"
            disabled={!picked || Boolean(validationError) || upload.isPending}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
          >
            {upload.isPending ? "Uploading..." : "Upload"}
          </button>
        </div>
      </form>
    </section>
  );
}
