import { useRef } from "react";

import {
  useAudioTasks,
  useDeleteAudio,
  useUploadAudio,
} from "@/hooks/useAudioTasks";

export function AudioListPage() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { data: tasks, isLoading, isError, error } = useAudioTasks();
  const upload = useUploadAudio();
  const remove = useDeleteAudio();

  const onPickFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) upload.mutate(file);
    e.target.value = ""; // allow re-uploading the same file
  };

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Audio tasks</h1>
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={upload.isPending}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
        >
          {upload.isPending ? "Uploading..." : "Upload"}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="audio/*"
          className="hidden"
          onChange={onPickFile}
        />
      </div>

      {upload.isError && (
        <p className="text-sm text-red-600">
          Upload failed: {(upload.error as Error).message}
        </p>
      )}

      {isLoading && <p className="text-sm text-slate-500">Loading tasks...</p>}
      {isError && (
        <p className="text-sm text-red-600">
          Failed to load: {(error as Error).message}
        </p>
      )}

      {tasks && tasks.length === 0 && (
        <p className="text-sm text-slate-500">No audio tasks yet.</p>
      )}

      {tasks && tasks.length > 0 && (
        <ul className="divide-y divide-slate-200 rounded-md border border-slate-200 bg-white">
          {tasks.map((t) => (
            <li
              key={t.id}
              className="flex items-center justify-between px-4 py-3"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-slate-900">
                  #{t.id} · {t.filename}
                </p>
                <p className="text-xs text-slate-500">{t.status}</p>
              </div>
              <button
                type="button"
                onClick={() => remove.mutate(t.id)}
                disabled={remove.isPending}
                className="text-sm text-red-600 hover:text-red-800 disabled:opacity-50"
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
