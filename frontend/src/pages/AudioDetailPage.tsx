import { Link, useNavigate, useParams } from "react-router-dom";

import { StatusBadge } from "@/components/StatusBadge";
import { useAudioTask, useDeleteAudio } from "@/hooks/useAudioTasks";

export function AudioDetailPage() {
  const { id } = useParams<{ id: string }>();
  const taskId = Number(id);
  const navigate = useNavigate();

  const { data: task, isLoading, isError, error } = useAudioTask(taskId);
  const remove = useDeleteAudio();

  if (!Number.isFinite(taskId)) {
    return (
      <p className="text-sm text-red-600">Invalid task id: {id}</p>
    );
  }

  if (isLoading) {
    return <p className="text-sm text-slate-500">Loading task #{taskId}...</p>;
  }
  if (isError) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-red-600">
          Failed to load: {(error as Error).message}
        </p>
        <Link
          to="/audio"
          className="inline-block text-sm text-slate-600 underline hover:text-slate-900"
        >
          Back to list
        </Link>
      </div>
    );
  }
  if (!task) {
    return <p className="text-sm text-slate-500">Task not found.</p>;
  }

  const onDelete = () => {
    if (!confirm(`Delete task #${task.id} (${task.filename})?`)) return;
    remove.mutate(task.id, { onSuccess: () => navigate("/audio") });
  };

  return (
    <section className="max-w-2xl space-y-6">
      <nav className="text-sm text-slate-500">
        <Link to="/audio" className="hover:text-slate-900 hover:underline">
          ← Back to tasks
        </Link>
      </nav>

      <header className="space-y-2">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight">Task #{task.id}</h1>
          <StatusBadge status={task.status} />
        </div>
        <p className="break-all text-sm text-slate-600">{task.filename}</p>
      </header>

      <dl className="grid grid-cols-1 gap-4 rounded-lg border border-slate-200 bg-white p-6 sm:grid-cols-2">
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
            ID
          </dt>
          <dd className="mt-1 text-sm text-slate-900">{task.id}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Status
          </dt>
          <dd className="mt-1 text-sm text-slate-900">{task.status}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Filename
          </dt>
          <dd className="mt-1 break-all text-sm text-slate-900">
            {task.filename}
          </dd>
        </div>
      </dl>

      <div className="flex items-center justify-end">
        <button
          type="button"
          onClick={onDelete}
          disabled={remove.isPending}
          className="rounded-md border border-red-200 bg-white px-4 py-2 text-sm font-medium text-red-600 hover:bg-red-50 disabled:opacity-50"
        >
          {remove.isPending ? "Deleting..." : "Delete task"}
        </button>
      </div>
    </section>
  );
}
