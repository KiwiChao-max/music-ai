import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ProgressBar } from "@/components/ProgressBar";
import { StemList } from "@/components/StemPlayer";
import { StatusBadge } from "@/components/StatusBadge";
import {
  useAudioTask,
  useDeleteAudio,
  useStartProcess,
  useStems,
} from "@/hooks/useAudioTasks";

const POLL_MS = 1500;

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  const s = (seconds - m * 60).toFixed(1);
  return `${m}m ${s}s`;
}

function formatTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString();
}

export function AudioDetailPage() {
  const { id } = useParams<{ id: string }>();
  const taskId = Number(id);
  const navigate = useNavigate();

  // Keep polling while the task is in flight; stop once it's terminal.
  const [polling, setPolling] = useState(true);
  const { data: task, isLoading, isError, error } = useAudioTask(taskId, {
    refetchInterval: polling ? POLL_MS : false,
  });
  useEffect(() => {
    if (task && task.status !== "PROCESSING") setPolling(false);
  }, [task]);

  const startProcess = useStartProcess();
  const remove = useDeleteAudio();

  // Stems are only meaningful once the task is FINISHED. The hook no-ops
  // otherwise; the GET would 409 anyway.
  const stemsQuery = useStems(taskId, task?.status === "FINISHED");

  if (!Number.isFinite(taskId)) {
    return <p className="text-sm text-red-600">Invalid task id: {id}</p>;
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

  const onStart = () => {
    setPolling(true); // resume polling so the progress bar appears immediately
    startProcess.mutate(task.id);
  };

  const isFinished = task.status === "FINISHED";
  const isFailed = task.status === "FAILED";
  const isProcessing = task.status === "PROCESSING";
  const isUploaded = task.status === "UPLOADED";

  return (
    <section className="max-w-2xl space-y-6">
      <nav className="text-sm text-slate-500">
        <Link to="/audio" className="hover:text-slate-900 hover:underline">
          ← Back to tasks
        </Link>
      </nav>

      {/* Hero: song name + status, as in the design spec. */}
      <header className="space-y-3">
        <h1 className="break-all text-3xl font-bold tracking-tight text-slate-900">
          {task.filename}
        </h1>
        <div className="flex items-center gap-2 text-sm text-slate-600">
          <span className="text-slate-500">状态：</span>
          {isFinished ? (
            <span className="inline-flex items-center gap-1.5 text-base font-semibold text-emerald-700">
              <span aria-hidden>✅</span> Finished
            </span>
          ) : (
            <StatusBadge status={task.status} />
          )}
        </div>
      </header>

      {/* State 1: UPLOADED — show Start button. */}
      {isUploaded && (
        <div className="rounded-lg border border-slate-200 bg-white p-6 text-center">
          <p className="text-sm text-slate-600">
            任务已上传，尚未开始处理。
          </p>
          <button
            type="button"
            onClick={onStart}
            disabled={startProcess.isPending}
            className="mt-3 rounded-md bg-slate-900 px-5 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
          >
            {startProcess.isPending ? "Starting..." : "Start processing"}
          </button>
        </div>
      )}

      {/* State 2: PROCESSING — show progress. */}
      {isProcessing && (
        <div className="space-y-2 rounded-lg border border-slate-200 bg-white p-6">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Processing
            </span>
            <span className="text-xs text-slate-500">live</span>
          </div>
          <ProgressBar value={task.progress} className="mt-1" />
          <p className="text-sm text-slate-700">
            {task.current_step ?? "Starting..."}
          </p>
        </div>
      )}

      {/* State 3: FAILED — show error + retry. */}
      {isFailed && (
        <div className="space-y-3 rounded-lg border border-red-200 bg-red-50 p-6">
          <p className="text-sm font-semibold text-red-700">处理失败</p>
          {task.error_message && (
            <pre className="overflow-x-auto rounded bg-white p-3 text-xs text-red-700">
              {task.error_message}
            </pre>
          )}
          <button
            type="button"
            onClick={onStart}
            disabled={startProcess.isPending}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
          >
            {startProcess.isPending ? "Retrying..." : "Retry"}
          </button>
        </div>
      )}

      {/* State 4: FINISHED — show stems. */}
      {isFinished && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold text-slate-900">分轨结果</h2>
          {stemsQuery.isLoading && (
            <p className="text-sm text-slate-500">Loading stems...</p>
          )}
          {stemsQuery.isError && (
            <p className="text-sm text-red-600">
              Failed to load stems: {(stemsQuery.error as Error).message}
            </p>
          )}
          {stemsQuery.data && stemsQuery.data.length === 0 && (
            <p className="text-sm text-slate-500">No stems produced.</p>
          )}
          {stemsQuery.data && stemsQuery.data.length > 0 && (
            <StemList stems={stemsQuery.data} />
          )}
        </section>
      )}

      {/* Metadata + delete — collapsed for FINISHED tasks, full for others. */}
      <dl className="grid grid-cols-1 gap-4 rounded-lg border border-slate-200 bg-white p-6 sm:grid-cols-2">
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
            ID
          </dt>
          <dd className="mt-1 text-sm text-slate-900">{task.id}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Duration
          </dt>
          <dd className="mt-1 text-sm text-slate-900">
            {formatDuration(task.duration)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Finished at
          </dt>
          <dd className="mt-1 text-sm text-slate-900">
            {formatTime(task.finished_at)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Status
          </dt>
          <dd className="mt-1 text-sm text-slate-900">{task.status}</dd>
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
