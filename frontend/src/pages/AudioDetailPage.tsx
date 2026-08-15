import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";

import { AnalysisPanel } from "@/components/AnalysisPanel";
import { CommentaryCard } from "@/components/CommentaryCard";
import { DrumPartPanel } from "@/components/DrumPartPanel";
import { Skeleton, EmptyState, ErrorState } from "@/components/States";
import { StemMixer } from "@/components/StemMixer";
import { TaskStatusPanel } from "@/components/TaskStatusPanel";
import { SampleBasedDrumPlayer } from "@/components/SampleBasedDrumPlayer";
import { instrumentsApi, type SampleLibraryInfo } from "@/api/instruments";
import {
  useAudioTask,
  useDeleteAudio,
  useMusicAnalysis,
  useStartProcess,
  useStems,
} from "@/hooks/useAudioTasks";
import { useTaskProgress } from "@/hooks/useTaskProgress";

// Slow fallback poll in case the WebSocket never connects.
const POLL_MS = 5000;

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "-";
  const m = Math.floor(seconds / 60);
  const s = (seconds - m * 60).toFixed(1);
  return `${m}m ${s}s`;
}

function formatTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString();
}

export function AudioDetailPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const taskId = Number(id);
  const navigate = useNavigate();

  const {
    data: task,
    isLoading,
    isError,
    error,
    refetch,
  } = useAudioTask(taskId, {
    refetchInterval: (current) => (current?.status === "PROCESSING" ? POLL_MS : false),
  });

  useTaskProgress(taskId, { enabled: task?.status === "PROCESSING" });

  const startProcess = useStartProcess();
  const remove = useDeleteAudio();
  const stemsQuery = useStems(taskId, task?.status === "FINISHED");
  const analysisQuery = useMusicAnalysis(taskId, task?.status === "FINISHED");
  const activeLibraryQuery = useQuery<SampleLibraryInfo | null>({
    queryKey: ["sample-libraries", "active"],
    queryFn: instrumentsApi.active,
    enabled: task?.status === "FINISHED",
  });

  // ---- Loading / error / not-found states ----

  if (!Number.isFinite(taskId)) {
    return (
      <ErrorState title={t("errors.generic")} error={t("detail.invalidId", { id: String(id) })} />
    );
  }
  if (isLoading) {
    return (
      <section className="max-w-3xl space-y-6">
        <Skeleton width="w-24" height="h-4" />
        <div className="space-y-3">
          <Skeleton width="w-2/3" height="h-8" />
          <Skeleton width="w-1/3" height="h-5" />
        </div>
        <div className="space-y-2 rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-6">
          <Skeleton width="w-1/4" height="h-4" />
          <Skeleton width="w-full" height="h-2" />
          <Skeleton width="w-1/2" height="h-4" />
        </div>
      </section>
    );
  }
  if (isError) {
    return (
      <section className="max-w-3xl space-y-4">
        <ErrorState title={t("detail.error")} error={error} onRetry={() => refetch()} />
        <Link
          to="/audio"
          className="inline-block text-sm text-slate-600 dark:text-slate-400 underline hover:text-slate-900 dark:hover:text-slate-100"
        >
          {t("detail.backToList")}
        </Link>
      </section>
    );
  }
  if (!task) {
    return (
      <EmptyState
        title={t("detail.notFound.title")}
        description={t("detail.notFound.description", { id: taskId })}
        action={
          <Link
            to="/audio"
            className="inline-flex items-center justify-center rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
          >
            {t("detail.backToList")}
          </Link>
        }
      />
    );
  }

  const isFinished = task.status === "FINISHED";

  const onDelete = () => {
    if (!confirm(t("detail.deleteConfirm", { id: task.id, name: task.filename }))) return;
    remove.mutate(task.id, { onSuccess: () => navigate("/audio") });
  };

  const onStart = () => {
    startProcess.reset();
    startProcess.mutate(task.id);
  };

  // ---- Main render ----

  return (
    <section className="max-w-3xl space-y-6">
      <nav className="text-sm text-slate-500 dark:text-slate-400">
        <Link
          to="/audio"
          className="hover:text-slate-900 dark:hover:text-slate-100 hover:underline"
        >
          {t("detail.backToList")}
        </Link>
      </nav>

      <header className="space-y-3">
        <h1 className="break-all text-3xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
          {task.filename}
        </h1>
      </header>

      {/* Task status (uploaded / processing / failed / finished) */}
      <TaskStatusPanel
        task={task}
        onStart={onStart}
        isStarting={startProcess.isPending}
        startError={startProcess.error}
        startRetry={onStart}
      />

      {/* Finished: stems, drums, analysis */}
      {isFinished && (
        <>
          <section className="space-y-3">
            {stemsQuery.isLoading && (
              <div
                className="space-y-2 rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4"
                aria-label={t("detail.stems.loading")}
              >
                <Skeleton width="w-1/3" />
                <Skeleton width="w-2/3" />
                <Skeleton width="w-1/2" />
              </div>
            )}
            {stemsQuery.isError && (
              <ErrorState
                title={t("detail.stems.error")}
                error={stemsQuery.error}
                onRetry={() => stemsQuery.refetch()}
              />
            )}
            {stemsQuery.data && stemsQuery.data.length === 0 && (
              <EmptyState
                title={t("detail.stems.empty.title")}
                description={t("detail.stems.empty.description")}
              />
            )}
            {stemsQuery.data && stemsQuery.data.length > 0 && <StemMixer stems={stemsQuery.data} />}
          </section>

          {stemsQuery.data && <DrumPartPanel stems={stemsQuery.data} />}

          <SampleBasedDrumPlayer
            eventsUrl={`/tasks/${task.id}/files/output/drums_events.json`}
            library={activeLibraryQuery.data ?? null}
            forceShow
          />

          {analysisQuery.isLoading && (
            <div
              className="space-y-3 rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4"
              aria-label={t("detail.analysis.loading")}
            >
              <Skeleton width="w-1/3" height="h-5" />
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {Array.from({ length: 4 }).map((_, idx) => (
                  <div
                    key={idx}
                    className="space-y-2 rounded-lg border border-slate-200 dark:border-slate-800 p-4"
                  >
                    <Skeleton width="w-1/2" height="h-3" />
                    <Skeleton width="w-3/4" height="h-6" />
                    <Skeleton width="w-1/3" height="h-3" />
                  </div>
                ))}
              </div>
            </div>
          )}
          {analysisQuery.isError && (
            <ErrorState title={t("detail.analysis.error")} error={analysisQuery.error} />
          )}
          {analysisQuery.data && (
            <>
              <CommentaryCard analysis={analysisQuery.data} />
              <AnalysisPanel analysis={analysisQuery.data} />
            </>
          )}
        </>
      )}

      {/* Metadata */}
      <dl className="grid grid-cols-1 gap-4 rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-6 sm:grid-cols-2">
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {t("detail.fields.id")}
          </dt>
          <dd className="mt-1 text-sm text-slate-900 dark:text-slate-100">{task.id}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {t("detail.fields.duration")}
          </dt>
          <dd className="mt-1 text-sm text-slate-900 dark:text-slate-100">
            {formatDuration(task.duration)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {t("detail.fields.finishedAt")}
          </dt>
          <dd className="mt-1 text-sm text-slate-900 dark:text-slate-100">
            {formatTime(task.finished_at)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {t("detail.fields.status")}
          </dt>
          <dd className="mt-1 text-sm text-slate-900 dark:text-slate-100">{task.status}</dd>
        </div>
      </dl>

      <div className="flex items-center justify-end">
        <button
          type="button"
          onClick={onDelete}
          disabled={remove.isPending}
          className="rounded-md border border-red-200 dark:border-red-800 bg-white px-4 py-2 text-sm font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 disabled:opacity-50"
        >
          {remove.isPending ? t("detail.deleting") : t("detail.delete")}
        </button>
      </div>
      {remove.isError && (
        <ErrorState title={t("detail.deleteError")} error={remove.error} onRetry={onDelete} />
      )}
    </section>
  );
}
