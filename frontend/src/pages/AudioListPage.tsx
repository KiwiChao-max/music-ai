import { Link } from "react-router-dom";

import { ProgressBar } from "@/components/ProgressBar";
import { StatusBadge } from "@/components/StatusBadge";
import { useAudioTasks } from "@/hooks/useAudioTasks";

export function AudioListPage() {
  const { data: tasks, isLoading, isError, error } = useAudioTasks();

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight">Audio tasks</h1>
          <p className="text-sm text-slate-600">
            All uploaded audio files. Click a row to view details.
          </p>
        </div>
        <Link
          to="/upload"
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
        >
          New upload
        </Link>
      </div>

      {isLoading && <p className="text-sm text-slate-500">Loading tasks...</p>}
      {isError && (
        <p className="text-sm text-red-600">
          Failed to load: {(error as Error).message}
        </p>
      )}

      {tasks && tasks.length === 0 && (
        <div className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-12 text-center">
          <p className="text-sm text-slate-600">No audio tasks yet.</p>
          <Link
            to="/upload"
            className="mt-3 inline-block text-sm font-medium text-slate-900 underline"
          >
            Upload your first file
          </Link>
        </div>
      )}

      {tasks && tasks.length > 0 && (
        <ul className="divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200 bg-white">
          {tasks.map((t) => (
            <li key={t.id}>
              <Link
                to={`/audio/${t.id}`}
                className="block px-4 py-3 transition-colors hover:bg-slate-50"
              >
                <div className="flex items-center justify-between gap-3">
                  <p className="min-w-0 flex-1 truncate text-sm font-medium text-slate-900">
                    #{t.id} · {t.filename}
                  </p>
                  <StatusBadge status={t.status} />
                </div>
                {t.status === "PROCESSING" && (
                  <div className="mt-2">
                    <ProgressBar value={t.progress} />
                    {t.current_step && (
                      <p className="mt-1 truncate text-xs text-slate-500">
                        {t.current_step}
                      </p>
                    )}
                  </div>
                )}
                {t.status === "FAILED" && t.error_message && (
                  <p className="mt-1 truncate text-xs text-red-600">
                    {t.error_message}
                  </p>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
