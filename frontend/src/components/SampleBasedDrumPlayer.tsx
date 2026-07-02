import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { instrumentsApi, type SampleLibraryInfo } from "@/api/instruments";
import { api } from "@/api/axios";

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
  eventsUrl: string | null; // /storage/outputs/task_X/drums_events.json
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
  const buffersRef = useRef<Map<number, AudioBuffer>>(new Map());
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
      return;
    }
    const ac = ensureContext(contextRef);
    masterRef.current = ac.createGain();
    masterRef.current.gain.value = 0.9;
    masterRef.current.connect(ac.destination);

    let cancelled = false;
    (async () => {
      try {
        const decoded = new Map<number, AudioBuffer>();
        // Dedup by midi note — many libraries have multiple round-robins
        // mapped to the same note, but we only need one to play it back.
        const seenNotes = new Set<number>();
        for (const file of library.files) {
          if (seenNotes.has(file.midi_note)) continue;
          seenNotes.add(file.midi_note);
          const url = instrumentsApi.sampleUrl(library.id, file.midi_note);
          const response = await api.get<ArrayBuffer>(url, {
            responseType: "arraybuffer",
          });
          if (cancelled) return;
          const buffer = await ac.decodeAudioData(response.data);
          if (cancelled) return;
          decoded.set(file.midi_note, buffer);
        }
        if (cancelled) return;
        buffersRef.current = decoded;
      } catch (err) {
        if (!cancelled) {
          setState({
            kind: "error",
            message: err instanceof Error ? err.message : "failed to load samples",
          });
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [library, hasSamples]);

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
          setState({
            kind: "error",
            message: err instanceof Error ? err.message : "failed to load events",
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [eventsUrl]);

  // Stop all scheduled sources — used on pause and on unmount.
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
      if (!master || events.length === 0) return;

      // First event strictly in the future of `fromSeconds` — anything
      // before that we just skip (the user already heard it on the first
      // pass).
      const startContextTime = ac.currentTime + 0.05;
      playbackStartRef.current = { contextTime: startContextTime, songTime: fromSeconds };

      for (const event of events) {
        if (event.t < fromSeconds) continue;
        const buffer = buffers.get(event.note);
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

      <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div
          className="h-full bg-slate-700 transition-all dark:bg-slate-400"
          style={{ width: `${duration > 0 ? (position / duration) * 100 : 0}%` }}
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
