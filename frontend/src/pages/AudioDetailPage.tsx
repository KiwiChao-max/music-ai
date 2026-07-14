import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";

import { CommentaryCard } from "@/components/CommentaryCard";
import { ProgressBar } from "@/components/ProgressBar";
import { SampleBasedDrumPlayer } from "@/components/SampleBasedDrumPlayer";
import { Skeleton, EmptyState, ErrorState } from "@/components/States";
import { StemMixer } from "@/components/StemMixer";
import { StatusBadge } from "@/components/StatusBadge";
import { instrumentsApi, type SampleLibraryInfo } from "@/api/instruments";
import {
  useAudioTask,
  useDeleteAudio,
  useMusicAnalysis,
  useStartProcess,
  useStems,
} from "@/hooks/useAudioTasks";
import { useTaskProgress } from "@/hooks/useTaskProgress";
import type { DetectedInstrument, MusicAnalysis, SoundfontOverride, StemInfo } from "@/types/audio";

// Slow fallback poll in case the WebSocket never connects (e.g. the
// worker is on a different host that doesn't expose WS). The WS path
// is the primary live-update mechanism.
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

function formatRange(start: number, end: number): string {
  return `${start.toFixed(1)}s-${end.toFixed(1)}s`;
}

function confidenceLabel(value: number, t: (key: string) => string): string {
  if (value >= 0.66) return t("detail.analysis.confidenceHigh");
  if (value >= 0.33) return t("detail.analysis.confidenceMedium");
  return t("detail.analysis.confidenceLow");
}

function AnalysisPanel({ analysis }: { analysis: MusicAnalysis }) {
  const { t } = useTranslation();
  const keyLabel = analysis.key && analysis.scale
    ? `${analysis.key} ${analysis.scale}`
    : t("detail.analysis.unknown");

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
          {t("detail.analysis.title")}
        </h2>
        {analysis.warnings.length > 0 && (
          <span className="rounded bg-amber-100 dark:bg-amber-900/40 px-2 py-1 text-xs font-medium text-amber-800 dark:text-amber-200">
            {t(
              analysis.warnings.length === 1
                ? "detail.analysis.warning"
                : "detail.analysis.warningPlural",
              { count: analysis.warnings.length },
            )}
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {t("detail.analysis.bpm")}
          </p>
          <p className="mt-1 text-2xl font-semibold text-slate-900 dark:text-slate-100">{analysis.bpm ?? "-"}</p>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {confidenceLabel(analysis.bpm_confidence, t)}
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {t("detail.analysis.key")}
          </p>
          <p className="mt-1 text-lg font-semibold text-slate-900 dark:text-slate-100">{keyLabel}</p>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {confidenceLabel(analysis.key_confidence, t)}
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {t("detail.analysis.notes")}
          </p>
          <p className="mt-1 text-2xl font-semibold text-slate-900 dark:text-slate-100">{analysis.note_count}</p>
          <p className="text-xs text-slate-500 dark:text-slate-400">{analysis.pitch_range ?? "-"}</p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {t("detail.analysis.duration")}
          </p>
          <p className="mt-1 text-2xl font-semibold text-slate-900 dark:text-slate-100">{analysis.duration.toFixed(1)}s</p>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {t("detail.analysis.analysisWindow")}
          </p>
        </div>
      </div>

      {analysis.chords.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            {t("detail.analysis.chordMap")}
          </h3>
          <div className="mt-3 flex flex-wrap gap-2">
            {analysis.chords.map((chord) => (
              <span
                key={`${chord.start}-${chord.end}-${chord.chord}`}
                className="rounded border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/40 px-2.5 py-1.5 text-sm text-slate-700 dark:text-slate-300"
                title={`Confidence ${Math.round(chord.confidence * 100)}%`}
              >
                <span className="font-semibold text-slate-900 dark:text-slate-100">{chord.chord}</span>{" "}
                <span className="text-xs text-slate-500 dark:text-slate-400">{formatRange(chord.start, chord.end)}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {(analysis.detected_instruments?.length ?? 0) > 0 && (
        <DetectedInstrumentsPanel
          items={analysis.detected_instruments ?? []}
          dominant={analysis.dominant_instrument ?? null}
        />
      )}

      {(analysis.soundfont_overrides?.length ?? 0) > 0 && (
        <SoundfontOverridesPanel
          overrides={analysis.soundfont_overrides ?? []}
        />
      )}

      {analysis.sections.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            {t("detail.analysis.sections")}
          </h3>
          <div className="mt-3 space-y-2">
            {analysis.sections.map((section) => (
              <div key={section.label} className="rounded border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/40 p-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-semibold text-slate-900 dark:text-slate-100">
                    {t("detail.analysis.section", { label: section.label })}
                  </span>
                  <span className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">{section.energy}</span>
                </div>
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                  {formatRange(section.start, section.end)} ·{" "}
                  {t("detail.analysis.density", { value: section.density })}
                </p>
                <p className="mt-2 text-sm text-slate-700 dark:text-slate-300">{section.suggestion}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        <AdviceList title={t("detail.analysis.instrumentSuggestions")} items={analysis.instrumentation} />
        <AdviceList title={t("detail.analysis.arrangementSuggestions")} items={analysis.arrangement} />
      </div>

      {analysis.warnings.length > 0 && (
        <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-4">
          <h3 className="text-sm font-semibold text-amber-900 dark:text-amber-200">
            {t("detail.analysis.analysisNotes")}
          </h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-amber-900 dark:text-amber-200">
            {analysis.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function AdviceList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4">
      <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-700 dark:text-slate-300">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function formatInstrumentName(name: string): string {
  return name
    .split("_")
    .map((token) => token.charAt(0).toUpperCase() + token.slice(1))
    .join(" ");
}

function DetectedInstrumentsPanel({
  items,
  dominant,
}: {
  items: DetectedInstrument[];
  dominant: string | null;
}) {
  const { t } = useTranslation();
  // Render high-to-low so the dominant instrument sits on top.
  const sorted = [...items].sort((a, b) => b.probability - a.probability);
  return (
    <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          {t("detail.analysis.instruments")}
        </h3>
        {dominant && (
          <span className="rounded bg-indigo-100 dark:bg-indigo-900/40 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-indigo-700 dark:text-indigo-300">
            {t("detail.analysis.dominant", { name: formatInstrumentName(dominant) })}
          </span>
        )}
      </div>
      <ul className="mt-3 space-y-2">
        {sorted.map((item) => (
          <li key={item.instrument} className="space-y-1">
            <div className="flex items-center justify-between text-xs text-slate-600 dark:text-slate-400">
              <span className="font-medium text-slate-700 dark:text-slate-300">
                {formatInstrumentName(item.instrument)}
              </span>
              <span className="font-mono">
                {(item.probability * 100).toFixed(0)}%
              </span>
            </div>
            <div
              className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800"
              role="progressbar"
              aria-valuenow={Math.round(item.probability * 100)}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={formatInstrumentName(item.instrument)}
            >
              <div
                className="h-full bg-slate-700 dark:bg-slate-400"
                style={{ width: `${Math.max(2, item.probability * 100)}%` }}
                aria-hidden="true"
              />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SoundfontOverridesPanel({ overrides }: { overrides: SoundfontOverride[] }) {
  const { t } = useTranslation();
  return (
    <div className="rounded-lg border border-indigo-200 dark:border-indigo-800 bg-indigo-50/50 dark:bg-indigo-950/20 p-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          {t("detail.soundfont.title")}
        </h3>
        <span className="rounded bg-indigo-100 dark:bg-indigo-900/40 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-indigo-700 dark:text-indigo-300">
          {t("detail.soundfont.count", { count: overrides.length })}
        </span>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        {t("detail.soundfont.subtitle")}
      </p>
      <ul className="mt-3 space-y-2">
        {overrides.map((ov) => (
          <li
            key={`${ov.stem}-${ov.program}-${ov.bank_msb}-${ov.bank_lsb}`}
            className="flex items-center justify-between gap-3 rounded border border-indigo-200 dark:border-indigo-800 bg-white dark:bg-slate-900 px-3 py-2"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">
                {ov.label}
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {t("detail.soundfont.stem", { stem: ov.stem })} ·{" "}
                {t("detail.soundfont.bank", {
                  msb: ov.bank_msb,
                  lsb: ov.bank_lsb,
                  program: ov.program,
                })}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

const DRUM_PARTS = [
  "kick", "snare", "sidestick", "hihat_closed", "hihat_open",
  "tom_high", "tom_himid", "tom_lomid", "tom_low", "tom_floor",
  "crash", "ride", "china", "splash", "ride_bell",
  "tambourine", "cowbell", "percussion", "fill",
] as const;

function drumPartLabel(t: (key: string) => string, part: string): string {
  const key = `drumParts.${part}`;
  const translated = t(key);
  return translated === key ? part : translated;
}

function DrumPartPanel({ stems }: { stems: StemInfo[] }) {
  const { t } = useTranslation();
  const parts = stems
    .filter((s) => s.kind === "midi" && s.name.startsWith("drums_"))
    .map((s) => {
      const part = s.name.replace(/^drums_/, "");
      return { part, stem: s };
    })
    .filter((entry) => DRUM_PARTS.includes(entry.part as typeof DRUM_PARTS[number]))
    .sort((a, b) => DRUM_PARTS.indexOf(a.part as typeof DRUM_PARTS[number]) - DRUM_PARTS.indexOf(b.part as typeof DRUM_PARTS[number]));
  if (parts.length === 0) return null;
  return (
    <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4">
      <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
        {t("detail.drums.title")}
      </h3>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        {t("detail.drums.subtitle")}
      </p>
      <ul className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
        {parts.map(({ part, stem }) => (
          <li
            key={part}
            className="flex items-center justify-between rounded border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/40 px-2 py-1.5 text-xs"
          >
            <span className="truncate text-slate-700 dark:text-slate-300">
              {drumPartLabel(t, part)}
            </span>
            <a
              href={stem.url}
              download={`${stem.name}.mid`}
              className="rounded border border-slate-300 dark:border-slate-700 bg-white px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 dark:bg-slate-800"
            >
              .mid
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AudioDetailPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const taskId = Number(id);
  const navigate = useNavigate();

  // Derive the polling interval from the current task status. This is the
  // single source of truth: while PROCESSING we slow-poll as a safety net,
  // otherwise we don't. The WebSocket is the primary live-update path.
  const { data: task, isLoading, isError, error, refetch } = useAudioTask(taskId, {
    refetchInterval: (current) =>
      current?.status === "PROCESSING" ? POLL_MS : false,
  });

  // Live progress over WebSocket — patches the same query key as
  // `useAudioTask` so the UI updates in real time without polling.
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

  if (!Number.isFinite(taskId)) {
    return (
      <ErrorState
        title={t("errors.generic")}
        error={t("detail.invalidId", { id: String(id) })}
      />
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
        <ErrorState
          title={t("detail.error")}
          error={error}
          onRetry={() => refetch()}
        />
        <Link
          to="/audio"
          className="inline-block text-sm text-slate-600 dark:text-slate-400 underline hover:text-slate-900 dark:hover:text-slate-100 dark:text-slate-100"
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

  const onDelete = () => {
    if (!confirm(t("detail.deleteConfirm", { id: task.id, name: task.filename }))) return;
    remove.mutate(task.id, { onSuccess: () => navigate("/audio") });
  };

  const onStart = () => {
    startProcess.reset();
    startProcess.mutate(task.id, {
      onError: () => {
        // Refetch will pick up the FAILED state from the server; nothing
        // extra to do here.
      },
    });
  };

  const isFinished = task.status === "FINISHED";
  const isFailed = task.status === "FAILED";
  const isProcessing = task.status === "PROCESSING";
  const isUploaded = task.status === "UPLOADED";

  return (
    <section className="max-w-3xl space-y-6">
      <nav className="text-sm text-slate-500 dark:text-slate-400">
        <Link to="/audio" className="hover:text-slate-900 dark:hover:text-slate-100 dark:text-slate-100 hover:underline">
          {t("detail.backToList")}
        </Link>
      </nav>

      <header className="space-y-3">
        <h1 className="break-all text-3xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
          {task.filename}
        </h1>
        <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400">
          <span className="text-slate-500 dark:text-slate-400">{t("detail.status")}</span>
          {isFinished ? (
            <span className="text-base font-semibold text-emerald-700 dark:text-emerald-300">
              {t("detail.finished")}
            </span>
          ) : (
            <StatusBadge status={task.status} />
          )}
        </div>
      </header>

      {isUploaded && (
        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-6 text-center">
          <p className="text-sm text-slate-600 dark:text-slate-400">
            {t("detail.readyToProcess")}
          </p>
          <button
            type="button"
            onClick={onStart}
            disabled={startProcess.isPending}
            className="mt-3 rounded-md bg-slate-900 px-5 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
          >
            {startProcess.isPending ? t("detail.starting") : t("detail.startProcessing")}
          </button>
          {startProcess.isError && (
            <ErrorState
              title={t("detail.startError")}
              error={startProcess.error}
              onRetry={onStart}
            />
          )}
        </div>
      )}

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
            disabled={startProcess.isPending}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
          >
            {startProcess.isPending ? t("detail.retrying") : t("detail.retry")}
          </button>
          {startProcess.isError && (
            <ErrorState
              title={t("detail.retryError")}
              error={startProcess.error}
              onRetry={onStart}
            />
          )}
        </div>
      )}

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
            {stemsQuery.data && stemsQuery.data.length > 0 && (
              <StemMixer stems={stemsQuery.data} />
            )}
          </section>

          {stemsQuery.data && <DrumPartPanel stems={stemsQuery.data} />}

          <SampleBasedDrumPlayer
            eventsUrl={`/storage/outputs/task_${task.id}/drums_events.json`}
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
                  <div key={idx} className="space-y-2 rounded-lg border border-slate-200 dark:border-slate-800 p-4">
                    <Skeleton width="w-1/2" height="h-3" />
                    <Skeleton width="w-3/4" height="h-6" />
                    <Skeleton width="w-1/3" height="h-3" />
                  </div>
                ))}
              </div>
            </div>
          )}
          {analysisQuery.isError && (
            <ErrorState
              title={t("detail.analysis.error")}
              error={analysisQuery.error}
            />
          )}
          {analysisQuery.data && (
            <>
              <CommentaryCard analysis={analysisQuery.data} />
              <AnalysisPanel analysis={analysisQuery.data} />
            </>
          )}
        </>
      )}

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
          <dd className="mt-1 text-sm text-slate-900 dark:text-slate-100">{formatDuration(task.duration)}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {t("detail.fields.finishedAt")}
          </dt>
          <dd className="mt-1 text-sm text-slate-900 dark:text-slate-100">{formatTime(task.finished_at)}</dd>
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
        <ErrorState
          title={t("detail.deleteError")}
          error={remove.error}
          onRetry={onDelete}
        />
      )}
    </section>
  );
}
