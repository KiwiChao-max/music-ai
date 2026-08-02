import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { instrumentsApi, type SampleLibraryInfo } from "@/api/instruments";
import { api, ApiError } from "@/api/axios";

interface DrumEvent {
  t: number; // seconds from start
  note: number; // GM percussion note
  velocity: number; // 1..127
  part: string;
}

interface DrumEventList {
  bpm: number | null;
  events: DrumEvent[];
}

interface SampleBasedDrumPlayerProps {
  eventsUrl: string | null; // /api/tasks/{id}/files/output/drums_events.json
  library: SampleLibraryInfo | null;
  /** When true, the component renders even if the library is empty. */
  forceShow?: boolean;
}

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready" }
  | { kind: "error"; message: string };

/**
 * Sample-based drum player.
 *
 * Pulls the drum event list written by the worker (`drums_events.json`) and
 * the user-uploaded sample library, decodes each sample, and schedules the
 * hits with the Web Audio API so the user hears their own kit instead of
 * the default GM bank. Events are pre-sorted by `t`; the playback loop
 * walks the list and queues each note-on with `start(when, offset)` using
 * `AudioContext.currentTime` as the clock.
 */
export function SampleBasedDrumPlayer({
  eventsUrl,
  library,
  forceShow,
}: SampleBasedDrumPlayerProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<LoadState>({ kind: "idle" });
  const [isPlaying, setIsPlaying] = useState(false);
  const [position, setPosition] = useState(0); // current playback position (s)
  const [duration, setDuration] = useState(0);

  const contextRef = useRef<AudioContext | null>(null);
  // Cache: key = `${note}:${vMin}:${vMax}`, value = decoded AudioBuffer.
  const buffersRef = useRef<Map<string, AudioBuffer>>(new Map());
  // Velocity-layer metadata for each buffer key: { note, vMin, vMax }.
  const layerMetaRef = useRef<Map<string, { note: number; vMin: number; vMax: number }>>(
    new Map(),
  );
  const masterRef = useRef<GainNode | null>(null);
  // The list of nodes we've already scheduled so we can stop them on pause.
  const activeSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const playbackStartRef = useRef<{ contextTime: number; songTime: number } | null>(
    null,
  );
  const rafIdRef = useRef<number | null>(null);
  const eventListRef = useRef<DrumEventList | null>(null);

  const hasSamples = useMemo(
    () => (library ? library.files.length > 0 : false),
    [library],
  );

  // Decode samples for every note the library covers. We do this once per
  // (library, events) change; the buffers are cached on `buffersRef` and
  // reused across play / pause cycles.
  useEffect(() => {
    if (!library || !hasSamples) {
      buffersRef.current.clear();
      if (masterRef.current) {
        try { masterRef.current.disconnect(); } catch { /* ignore */ }
        masterRef.current = null;
      }
      return;
    }
    const ac = ensureContext(contextRef);
    // Disconnect the previous master GainNode to prevent orphan node leaks
    // when the library changes.
    if (masterRef.current) {
      try { masterRef.current.disconnect(); } catch { /* ignore */ }
    }
    masterRef.current = ac.createGain();
    masterRef.current.gain.value = 0.9;
    masterRef.current.connect(ac.destination);

    let cancelled = false;
    const abortController = new AbortController();
    (async () => {
      try {
        const decoded = new Map<string, AudioBuffer>();
        const meta = new Map<string, { note: number; vMin: number; vMax: number }>();
        for (const file of library.files) {
          const key = `${file.midi_note}:${file.velocity_min}:${file.velocity_max}`;
          if (decoded.has(key)) continue;
          const url = instrumentsApi.sampleUrl(library.id, file.midi_note);
          const response = await api.get<ArrayBuffer>(url, {
            responseType: "arraybuffer",
            signal: abortController.signal,
          });
          if (cancelled) return;
          const buffer = await ac.decodeAudioData(response.data);
          if (cancelled) return;
          decoded.set(key, buffer);
          meta.set(key, {
            note: file.midi_note,
            vMin: file.velocity_min ?? 1,
            vMax: file.velocity_max ?? 127,
          });
        }
        if (cancelled) return;
        buffersRef.current = decoded;
        layerMetaRef.current = meta;
      } catch (err) {
        if (!cancelled) {
          if (err instanceof ApiError && err.status === 404) {
            // Sample file not found on disk --- skip silently.
          } else {
            setState({
              kind: "error",
              message: err instanceof Error ? err.message : t("errors.loadSamples"),
            });
          }
        }
      }
    })();

    return () => {
      cancelled = true;
      abortController.abort();
    };
  }, [library, hasSamples, t]);

  // Fetch the event list whenever the URL changes.
  useEffect(() => {
    if (!eventsUrl) {
      eventListRef.current = null;
      setState({ kind: "idle" });
      return;
    }
    setState({ kind: "loading" });
    let cancelled = false;
    (async () => {
      try {
        const response = await api.get<DrumEventList>(eventsUrl);
        if (cancelled) return;
        const data = response.data;
        // Defensive sort: backend already sorts, but if a future change
        // doesn't, playback still works.
        data.events = [...data.events].sort((a, b) => a.t - b.t);
        eventListRef.current = data;
        const last = data.events[data.events.length - 1];
        setDuration(last ? last.t + 0.5 : 0);
        setState({ kind: "ready" });
      } catch (err) {
        if (!cancelled) {
          // 404 means the track simply has no drum events --- not a real error.
          if (err instanceof ApiError && err.status === 404) {
            setState({ kind: "idle" });
          } else {
            setState({
              kind: "error",
              message: err instanceof Error ? err.message : t("errors.loadEvents"),
            });
          }
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [eventsUrl, t]);

  // Stop all scheduled sources --- used on pause and on unmount.
  const stopAllScheduled = useCallback(() => {
    for (const source of activeSourcesRef.current) {
      try {
        source.stop();
      } catch {
        // already stopped
      }
    }
    activeSourcesRef.current.clear();
  }, []);

  const playFrom = useCallback(
    (fromSeconds: number) => {
      const ac = ensureContext(contextRef);
      const master = masterRef.current;
      const events = eventListRef.current?.events ?? [];
      const buffers = buffersRef.current;
      const meta = layerMetaRef.current;
      if (!master || events.length === 0) return;

      const startContextTime = ac.currentTime + 0.05;
      playbackStartRef.current = { contextTime: startContextTime, songTime: fromSeconds };

      for (const event of events) {
        if (event.t < fromSeconds) continue;
        const buffer = _pickBuffer(buffers, meta, event.note, event.velocity);
        if (!buffer) continue;
        const source = ac.createBufferSource();
        source.buffer = buffer;
        const gain = ac.createGain();
        // velocity 0..127 -> gain 0..1.0 with a small floor so quiet hits
        // are still audible in the browser preview.
        const v = Math.max(1, Math.min(127, event.velocity)) / 127;
        gain.gain.value = Math.max(0.05, v);
        source.connect(gain).connect(master);
        const when = startContextTime + (event.t - fromSeconds);
        source.start(when);
        activeSourcesRef.current.add(source);
        source.onended = () => {
          activeSourcesRef.current.delete(source);
        };
      }
      setIsPlaying(true);
    },
    [],
  );

  const onPlay = useCallback(() => {
    if (state.kind !== "ready") return;
    if (isPlaying) return;
    playFrom(position);
  }, [state, isPlaying, playFrom, position]);

  const onPause = useCallback(() => {
    stopAllScheduled();
    setIsPlaying(false);
  }, [stopAllScheduled]);

  const onStop = useCallback(() => {
    stopAllScheduled();
    setIsPlaying(false);
    setPosition(0);
    playbackStartRef.current = null;
  }, [stopAllScheduled]);

  // Drive the playhead via requestAnimationFrame so the position bar
  // moves while audio plays.
  useEffect(() => {
    if (!isPlaying) return;
    const tick = () => {
      const ctx = contextRef.current;
      const pb = playbackStartRef.current;
      if (!ctx || !pb) return;
      const elapsed = ctx.currentTime - pb.contextTime;
      const next = pb.songTime + elapsed;
      if (next >= duration) {
        setPosition(duration);
        setIsPlaying(false);
        stopAllScheduled();
        return;
      }
      setPosition(next);
      rafIdRef.current = requestAnimationFrame(tick);
    };
    rafIdRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current);
        rafIdRef.current = null;
      }
    };
  }, [isPlaying, duration, stopAllScheduled]);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      stopAllScheduled();
      if (contextRef.current) {
        contextRef.current.close().catch(() => {
          // already closed
        });
        contextRef.current = null;
      }
    };
  }, [stopAllScheduled]);

  if (!forceShow && !library) return null;
  if (!forceShow && library && !hasSamples) return null;
  if (!eventsUrl) return null;

  const noteCount = new Set(library?.files.map((f) => f.midi_note) ?? []).size;
  const fileCount = library?.files.length ?? 0;
  const layerCount = library?.files.filter(
    (f) => (f.velocity_min ?? 1) > 1 || (f.velocity_max ?? 127) < 127,
  ).length ?? 0;

  return (
    <section className="space-y-4 rounded-lg border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
      <header className="space-y-1">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          {t("player.sampleBasedDrumPlayback")}
        </h3>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {library
            ? t("player.playingWithLibrary", { count: eventListRef.current?.events.length ?? 0, name: library.name })
            : t("player.noLibraryHint")}
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        {!isPlaying ? (
          <button
            type="button"
            onClick={onPlay}
            disabled={state.kind !== "ready" || (library !== null && !hasSamples)}
            className="inline-flex items-center justify-center rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
          >
            {position > 0 ? t("player.resume") : t("player.playWithMySamples")}
          </button>
        ) : (
          <button
            type="button"
            onClick={onPause}
            className="inline-flex items-center justify-center rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
          >
            {t("player.pause")}
          </button>
        )}
        <button
          type="button"
          onClick={onStop}
          disabled={position === 0 && !isPlaying}
          className="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
        >
          {t("player.stop")}
        </button>
        <span className="ml-auto font-mono text-xs text-slate-500 dark:text-slate-400">
          {position.toFixed(1)}s / {duration.toFixed(1)}s
        </span>
      </div>

      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800"
        role="progressbar"
        aria-valuenow={Math.round(duration > 0 ? (position / duration) * 100 : 0)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={t("player.sampleBasedDrumPlayback")}
      >
        <div
          className="h-full bg-slate-700 transition-all dark:bg-slate-400"
          style={{ width: `${duration > 0 ? (position / duration) * 100 : 0}%` }}
          aria-hidden="true"
        />
      </div>

      {state.kind === "loading" && (
        <p className="text-xs text-slate-500 dark:text-slate-400">{t("player.loadingDrumEvents")}</p>
      )}
      {state.kind === "error" && (
        <p className="text-xs text-red-600 dark:text-red-400">{t("player.playerError")}: {state.message}</p>
      )}
      {library && hasSamples && (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {t("player.samplesSummary", { fileCount, noteCount })}
          {layerCount > 0 && ` (${layerCount} velocity layers)`}
        </p>
      )}
      {library && !hasSamples && (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          {t("player.emptyLibraryHint")}
        </p>
      )}
    </section>
  );
}

function ensureContext(ref: React.MutableRefObject<AudioContext | null>): AudioContext {
  if (ref.current) return ref.current;
  const Ctor =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  ref.current = new Ctor();
  return ref.current;
}

/**
 * Pick the best-matching AudioBuffer for a given MIDI note and velocity.
 *
 * Strategy:
 * 1. Exact match: buffer whose [vMin, vMax] contains the velocity.
 * 2. Full-range fallback: buffer with vMin=1, vMax=127 for the same note.
 * 3. Closest layer: buffer whose range is nearest to the velocity.
 */
function _pickBuffer(
  buffers: Map<string, AudioBuffer>,
  meta: Map<string, { note: number; vMin: number; vMax: number }>,
  note: number,
  velocity: number,
): AudioBuffer | null {
  let bestKey: string | null = null;
  let bestScore = -1;
  // Full-range fallback for this note.
  let fallbackKey: string | null = null;

  for (const [key, info] of meta) {
    if (info.note !== note) continue;
    // Exact velocity range match
    if (velocity >= info.vMin && velocity <= info.vMax) {
      // Prefer the narrowest matching range (most specific layer).
      const width = info.vMax - info.vMin;
      const score = 1000 - width; // narrower = higher score
      if (score > bestScore) {
        bestScore = score;
        bestKey = key;
      }
    }
    // Track full-range fallback
    if (info.vMin === 1 && info.vMax === 127) {
      fallbackKey = key;
    }
  }

  if (bestKey !== null) return buffers.get(bestKey) ?? null;
  if (fallbackKey !== null) return buffers.get(fallbackKey) ?? null;
  return null;
}
