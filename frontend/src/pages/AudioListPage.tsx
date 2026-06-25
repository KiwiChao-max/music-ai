import { Link } from "react-router-dom";

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
                className="flex items-center justify-between px-4 py-3 transition-colors hover:bg-slate-50"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-slate-900">
                    #{t.id} · {t.filename}
                  </p>
                </div>
                <StatusBadge status={t.status} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
