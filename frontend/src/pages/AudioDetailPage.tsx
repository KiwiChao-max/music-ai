import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { ProgressBar } from "@/components/ProgressBar";
import { SampleBasedDrumPlayer } from "@/components/SampleBasedDrumPlayer";
import { StemList } from "@/components/StemPlayer";
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
import type { DetectedInstrument, MusicAnalysis, StemInfo } from "@/types/audio";

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

function confidenceLabel(value: number): string {
  if (value >= 0.66) return "high";
  if (value >= 0.33) return "medium";
  return "low";
}

function AnalysisPanel({ analysis }: { analysis: MusicAnalysis }) {
  const keyLabel = analysis.key && analysis.scale
    ? `${analysis.key} ${analysis.scale}`
    : "Unknown";

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-slate-900">AI Analysis</h2>
        {analysis.warnings.length > 0 && (
          <span className="rounded bg-amber-100 px-2 py-1 text-xs font-medium text-amber-800">
            {analysis.warnings.length} warning{analysis.warnings.length > 1 ? "s" : ""}
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">BPM</p>
          <p className="mt-1 text-2xl font-semibold text-slate-900">{analysis.bpm ?? "-"}</p>
          <p className="text-xs text-slate-500">{confidenceLabel(analysis.bpm_confidence)}</p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Key</p>
          <p className="mt-1 text-lg font-semibold text-slate-900">{keyLabel}</p>
          <p className="text-xs text-slate-500">{confidenceLabel(analysis.key_confidence)}</p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Notes</p>
          <p className="mt-1 text-2xl font-semibold text-slate-900">{analysis.note_count}</p>
          <p className="text-xs text-slate-500">{analysis.pitch_range ?? "-"}</p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Duration</p>
          <p className="mt-1 text-2xl font-semibold text-slate-900">{analysis.duration.toFixed(1)}s</p>
          <p className="text-xs text-slate-500">analysis window</p>
        </div>
      </div>

      {analysis.chords.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <h3 className="text-sm font-semibold text-slate-900">Chord Map</h3>
          <div className="mt-3 flex flex-wrap gap-2">
            {analysis.chords.map((chord) => (
              <span
                key={`${chord.start}-${chord.end}-${chord.chord}`}
                className="rounded border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-sm text-slate-700"
                title={`Confidence ${Math.round(chord.confidence * 100)}%`}
              >
                <span className="font-semibold text-slate-900">{chord.chord}</span>{" "}
                <span className="text-xs text-slate-500">{formatRange(chord.start, chord.end)}</span>
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

      {analysis.sections.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <h3 className="text-sm font-semibold text-slate-900">Sections</h3>
          <div className="mt-3 space-y-2">
            {analysis.sections.map((section) => (
              <div key={section.label} className="rounded border border-slate-200 bg-slate-50 p-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-semibold text-slate-900">Section {section.label}</span>
                  <span className="text-xs uppercase tracking-wide text-slate-500">{section.energy}</span>
                </div>
                <p className="mt-1 text-xs text-slate-500">{formatRange(section.start, section.end)} · density {section.density}</p>
                <p className="mt-2 text-sm text-slate-700">{section.suggestion}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        <AdviceList title="Instrument Suggestions" items={analysis.instrumentation} />
        <AdviceList title="Arrangement Suggestions" items={analysis.arrangement} />
      </div>

      {analysis.warnings.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
          <h3 className="text-sm font-semibold text-amber-900">Analysis Notes</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-amber-900">
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
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-700">
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
  // Render high-to-low so the dominant instrument sits on top.
  const sorted = [...items].sort((a, b) => b.probability - a.probability);
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-slate-900">
          Detected instruments
        </h3>
        {dominant && (
          <span className="rounded bg-indigo-100 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-indigo-700">
            Dominant: {formatInstrumentName(dominant)}
          </span>
        )}
      </div>
      <ul className="mt-3 space-y-2">
        {sorted.map((item) => (
          <li key={item.instrument} className="space-y-1">
            <div className="flex items-center justify-between text-xs text-slate-600">
              <span className="font-medium text-slate-700">
                {formatInstrumentName(item.instrument)}
              </span>
              <span className="font-mono">
                {(item.probability * 100).toFixed(0)}%
              </span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full bg-slate-700"
                style={{ width: `${Math.max(2, item.probability * 100)}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

const DRUM_PART_LABELS: Record<string, string> = {
  kick: "Kick",
  snare: "Snare",
  sidestick: "Side Stick",
  hihat_closed: "Closed Hi-Hat",
  hihat_open: "Open Hi-Hat",
  tom_high: "High Tom",
  tom_himid: "Hi-Mid Tom",
  tom_lomid: "Low-Mid Tom",
  tom_low: "Low Tom",
  tom_floor: "Floor Tom",
  crash: "Crash",
  ride: "Ride",
  china: "China",
  splash: "Splash",
  ride_bell: "Ride Bell",
  tambourine: "Tambourine",
  cowbell: "Cowbell",
  percussion: "Percussion",
  fill: "Fills",
};

function DrumPartPanel({ stems }: { stems: StemInfo[] }) {
  const parts = stems
    .filter((s) => s.kind === "midi" && s.name.startsWith("drums_"))
    .map((s) => {
      const part = s.name.replace(/^drums_/, "");
      return { part, stem: s };
    })
    .filter((entry) => entry.part in DRUM_PART_LABELS)
    .sort((a, b) => a.part.localeCompare(b.part));
  if (parts.length === 0) return null;
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-slate-900">
        Drum parts (per-instrument MIDI)
      </h3>
      <p className="mt-1 text-xs text-slate-500">
        Drop any of these into a DAW to edit a single component of the kit.
      </p>
      <ul className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
        {parts.map(({ part, stem }) => (
          <li
            key={part}
            className="flex items-center justify-between rounded border border-slate-200 bg-slate-50 px-2 py-1.5 text-xs"
          >
            <span className="truncate text-slate-700">
              {DRUM_PART_LABELS[part] ?? part}
            </span>
            <a
              href={stem.url}
              download={`${stem.name}.mid`}
              className="rounded border border-slate-300 bg-white px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-700 hover:bg-slate-100"
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
  const { id } = useParams<{ id: string }>();
  const taskId = Number(id);
  const navigate = useNavigate();

  // Derive the polling interval from the current task status. This is the
  // single source of truth: while PROCESSING we slow-poll as a safety net,
  // otherwise we don't. The WebSocket is the primary live-update path.
  const { data: task, isLoading, isError, error } = useAudioTask(taskId, {
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
    return <p className="text-sm text-red-600">Invalid task id: {id}</p>;
  }
  if (isLoading) {
    return <p className="text-sm text-slate-500">Loading task #{taskId}...</p>;
  }
  if (isError) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-red-600">
          Failed to load: {error.message}
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
      <nav className="text-sm text-slate-500">
        <Link to="/audio" className="hover:text-slate-900 hover:underline">
          Back to tasks
        </Link>
      </nav>

      <header className="space-y-3">
        <h1 className="break-all text-3xl font-bold tracking-tight text-slate-900">
          {task.filename}
        </h1>
        <div className="flex items-center gap-2 text-sm text-slate-600">
          <span className="text-slate-500">Status:</span>
          {isFinished ? (
            <span className="text-base font-semibold text-emerald-700">Finished</span>
          ) : (
            <StatusBadge status={task.status} />
          )}
        </div>
      </header>

      {isUploaded && (
        <div className="rounded-lg border border-slate-200 bg-white p-6 text-center">
          <p className="text-sm text-slate-600">The task is uploaded and ready to process.</p>
          <button
            type="button"
            onClick={onStart}
            disabled={startProcess.isPending}
            className="mt-3 rounded-md bg-slate-900 px-5 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
          >
            {startProcess.isPending ? "Starting..." : "Start processing"}
          </button>
          {startProcess.isError && (
            <p className="mt-3 text-sm text-red-600">
              Failed to start: {startProcess.error.message}
            </p>
          )}
        </div>
      )}

      {isProcessing && (
        <div className="space-y-2 rounded-lg border border-slate-200 bg-white p-6">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium uppercase tracking-wide text-slate-500">Processing</span>
            <span className="text-xs text-slate-500">live</span>
          </div>
          <ProgressBar value={task.progress} className="mt-1" />
          <p className="text-sm text-slate-700">{task.current_step ?? "Starting..."}</p>
        </div>
      )}

      {isFailed && (
        <div className="space-y-3 rounded-lg border border-red-200 bg-red-50 p-6">
          <p className="text-sm font-semibold text-red-700">Processing failed</p>
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
          {startProcess.isError && (
            <p className="text-sm text-red-700">
              Retry failed: {startProcess.error.message}
            </p>
          )}
        </div>
      )}

      {isFinished && (
        <>
          <section className="space-y-3">
            <h2 className="text-lg font-semibold text-slate-900">Output Files</h2>
            {stemsQuery.isLoading && <p className="text-sm text-slate-500">Loading stems...</p>}
            {stemsQuery.isError && (
              <p className="text-sm text-red-600">
                Failed to load stems: {stemsQuery.error.message}
              </p>
            )}
            {stemsQuery.data && stemsQuery.data.length === 0 && (
              <p className="text-sm text-slate-500">No stems produced.</p>
            )}
            {stemsQuery.data && stemsQuery.data.length > 0 && (
              <StemList stems={stemsQuery.data} />
            )}
          </section>

          {stemsQuery.data && <DrumPartPanel stems={stemsQuery.data} />}

          <SampleBasedDrumPlayer
            eventsUrl={`/storage/outputs/task_${task.id}/drums_events.json`}
            library={activeLibraryQuery.data ?? null}
          />

          {analysisQuery.isLoading && (
            <p className="text-sm text-slate-500">Loading music analysis...</p>
          )}
          {analysisQuery.isError && (
            <p className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
              Analysis is not available for this task yet.
            </p>
          )}
          {analysisQuery.data && <AnalysisPanel analysis={analysisQuery.data} />}
        </>
      )}

      <dl className="grid grid-cols-1 gap-4 rounded-lg border border-slate-200 bg-white p-6 sm:grid-cols-2">
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">ID</dt>
          <dd className="mt-1 text-sm text-slate-900">{task.id}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">Duration</dt>
          <dd className="mt-1 text-sm text-slate-900">{formatDuration(task.duration)}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">Finished at</dt>
          <dd className="mt-1 text-sm text-slate-900">{formatTime(task.finished_at)}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">Status</dt>
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
      {remove.isError && (
        <p className="text-right text-sm text-red-600">
          Delete failed: {remove.error.message}
        </p>
      )}
    </section>
  );
}
