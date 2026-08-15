import { useTranslation } from "react-i18next";

import { ProgressBar } from "@/components/ProgressBar";
import { ErrorState } from "@/components/States";
import type { AudioTask } from "@/types/audio";

interface TaskStatusPanelProps {
  task: AudioTask;
  /** Called when the user clicks "Start Processing" or "Retry". */
  onStart: () => void;
  isStarting: boolean;
  /** Mutation error from `useStartProcess` (react-query v5 types it `Error | null`). */
  startError: Error | null;
  startRetry: () => void;
}

export function TaskStatusPanel({
  task,
  onStart,
  isStarting,
  startError,
  startRetry,
}: TaskStatusPanelProps) {
  const { t } = useTranslation();
  const isUploaded = task.status === "UPLOADED";
  const isProcessing = task.status === "PROCESSING";
  const isFailed = task.status === "FAILED";
  const isFinished = task.status === "FINISHED";

  return (
    <>
      {/* Uploaded: show "Start Processing" button */}
      {isUploaded && (
        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-6 text-center">
          <p className="text-sm text-slate-600 dark:text-slate-400">{t("detail.readyToProcess")}</p>
          <button
            type="button"
            onClick={onStart}
            disabled={isStarting}
            className="mt-3 rounded-md bg-slate-900 px-5 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
          >
            {isStarting ? t("detail.starting") : t("detail.startProcessing")}
          </button>
          {startError && (
            <ErrorState title={t("detail.startError")} error={startError} onRetry={startRetry} />
          )}
        </div>
      )}

      {/* Processing: live progress bar */}
      {isProcessing && (
        <div className="space-y-2 rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-6">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
              {t("detail.processing")}
            </span>
            <span className="text-xs text-slate-500 dark:text-slate-400">{t("detail.live")}</span>
          </div>
          <ProgressBar value={task.progress} className="mt-1" />
          <p className="text-sm text-slate-700 dark:text-slate-300">
            {task.current_step ?? t("detail.starting")}
          </p>
        </div>
      )}

      {/* Failed: error message + retry button */}
      {isFailed && (
        <div className="space-y-3 rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-6">
          <p className="text-sm font-semibold text-red-700 dark:text-red-300">
            {t("detail.processingFailed")}
          </p>
          {task.error_message && (
            <pre className="overflow-x-auto rounded bg-white p-3 text-xs text-red-700 dark:text-red-300">
              {task.error_message}
            </pre>
          )}
          <button
            type="button"
            onClick={onStart}
            disabled={isStarting}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
          >
            {isStarting ? t("detail.retrying") : t("detail.retry")}
          </button>
          {startError && (
            <ErrorState title={t("detail.retryError")} error={startError} onRetry={startRetry} />
          )}
        </div>
      )}

      {/* Finished status badge */}
      {isFinished && (
        <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400">
          <span className="text-slate-500 dark:text-slate-400">{t("detail.status")}</span>
          <span className="text-base font-semibold text-emerald-700 dark:text-emerald-300">
            {t("detail.finished")}
          </span>
        </div>
      )}
    </>
  );
}
