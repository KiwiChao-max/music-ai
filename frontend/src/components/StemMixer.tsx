/**
 * Per-stem wavesurfer.js waveform preview.
 *
 * Each row shows a compact waveform of an audio stem. The play/pause
 * button hands off to the global player (so the click in this row opens
 * the bottom transport bar), and the waveform itself highlights the
 * progress while the track is playing. A small download button mirrors
 * the row in the metadata list.
 *
 * Why not one wavesurfer per stem? `N` wavesurfer instances would each
 * decode the entire file off the network, which is wasteful for the
 * 4-stem case. Instead this component lazy-mounts its wavesurfer when
 * the row scrolls into view (`IntersectionObserver`) so we only pay
 * the decode cost for stems the user is actually looking at.
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import WaveSurfer from "wavesurfer.js";

import { usePlayer } from "@/contexts/PlayerContext";
import { MidiPreviewPlayer } from "@/components/MidiPreviewPlayer";
import type { StemInfo } from "@/types/audio";

function stemLabel(t: (key: string) => string, name: string): string {
  const key = `stems.${name}`;
  const translated = t(key);
  return translated === key ? name : translated;
}

interface StemRowProps {
  stem: StemInfo;
  isCurrent: boolean;
  isPlaying: boolean;
  onPlay: (stem: StemInfo) => void;
}

function StemWaveform({ url, reloadKey }: { url: string; reloadKey: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    const dark = document.documentElement.classList.contains("dark");
    const waveColor = dark ? "#475569" : "#cbd5e1";
    const progressColor = dark ? "#e2e8f0" : "#0f172a";
    const cursorColor = dark ? "#e2e8f0" : "#0f172a";
    const ws = WaveSurfer.create({
      container,
      url,
      height: 36,
      waveColor,
      progressColor,
      cursorColor,
      cursorWidth: 1,
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      normalize: true,
      autoplay: false,
      interact: false,
    });
    return () => {
      ws.destroy();
    };
  }, [reloadKey, url]);
  return <div ref={containerRef} className="h-9 w-full" />;
}

const StemRow = memo(function StemRow({ stem, isCurrent, isPlaying, onPlay }: StemRowProps) {
  const { t } = useTranslation();
  const label = stemLabel(t, stem.name);
  const artifactUrl = stem.url;
  return (
    <li className="grid grid-cols-[auto_1fr_auto] items-center gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex w-32 shrink-0 flex-col gap-1">
        <span className="truncate text-sm font-semibold capitalize text-slate-900 dark:text-slate-100">
          {label}
        </span>
        <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          {t("common.audio")}
        </span>
      </div>
      <div className="min-w-0">
        <StemWaveform url={artifactUrl} reloadKey={stem.url} />
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <button
          type="button"
          onClick={() => onPlay(stem)}
          className={`inline-flex min-w-20 items-center justify-center rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
            isCurrent && isPlaying
              ? "bg-emerald-600 text-white hover:bg-emerald-700"
              : "bg-slate-900 text-white hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
          }`}
          aria-label={
            isCurrent && isPlaying
              ? `${t("player.pause")} ${label}`
              : `${t("player.play")} ${label}`
          }
        >
          {isCurrent && isPlaying ? t("player.pause") : t("player.play")}
        </button>
        <a
          href={artifactUrl}
          download={`${stem.name}.wav`}
          className="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
        >
          {t("common.download")}
        </a>
      </div>
    </li>
  );
});

interface StemSkeletonProps {
  stem: StemInfo;
  onPlay: (stem: StemInfo) => void;
}

function StemSkeleton({ stem, onPlay }: StemSkeletonProps) {
  const { t } = useTranslation();
  const label = stemLabel(t, stem.name);
  const artifactUrl = stem.url;
  return (
    <div className="grid h-[68px] grid-cols-[auto_1fr_auto] items-center gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
      <div className="w-32 shrink-0">
        <p className="truncate text-sm font-semibold capitalize text-slate-900 dark:text-slate-100">
          {label}
        </p>
        <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          {t("common.audio")}
        </p>
      </div>
      <div className="h-9 w-full animate-pulse rounded bg-slate-100 dark:bg-slate-800" />
      <div className="flex shrink-0 items-center gap-2">
        <button
          type="button"
          onClick={() => onPlay(stem)}
          className="inline-flex min-w-20 items-center justify-center rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
        >
          {t("player.play")}
        </button>
        <a
          href={artifactUrl}
          download={`${stem.name}.wav`}
          className="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
        >
          {t("common.download")}
        </a>
      </div>
    </div>
  );
}

interface MidiStemRowProps {
  stem: StemInfo;
  midiProfileLabel: (profile?: string) => string;
}

const MidiStemRow = memo(function MidiStemRow({ stem, midiProfileLabel }: MidiStemRowProps) {
  const { t } = useTranslation();
  const artifactUrl = stem.url;
  return (
    <li className="flex items-center justify-between gap-2 rounded border border-slate-200 bg-slate-50 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800">
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate font-medium text-slate-700 dark:text-slate-300">
          {stemLabel(t, stem.name)}
        </span>
        {stem.profile && (
          <span className="shrink-0 rounded bg-slate-200 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-600 dark:bg-slate-700 dark:text-slate-400">
            {midiProfileLabel(stem.profile)}
          </span>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <div className="w-36">
          <MidiPreviewPlayer url={artifactUrl} />
        </div>
        <a
          href={artifactUrl}
          download={`${stem.name}.mid`}
          className="inline-flex items-center rounded border border-slate-300 bg-white px-2 py-1 text-xs font-semibold uppercase tracking-wide text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:border-slate-600 dark:bg-slate-700 dark:text-slate-300 dark:hover:bg-slate-600 dark:hover:text-slate-100"
        >
          {t("common.download")}
        </a>
      </div>
    </li>
  );
});

interface StemMixerProps {
  stems: StemInfo[];
}

export function StemMixer({ stems }: StemMixerProps) {
  const { t } = useTranslation();
  // Filter once per `stems` change. These arrays feed an IntersectionObserver
  // effect below; a fresh array identity per render would tear the observer
  // down and recreate it on every re-render (4x/sec during playback).
  const audioStems = useMemo(() => stems.filter((s) => s.kind === "audio"), [stems]);
  const midiStems = useMemo(() => stems.filter((s) => s.kind === "midi"), [stems]);
  const { current, isPlaying, play, pause } = usePlayer();

  const [activated, setActivated] = useState<Set<string>>(new Set());
  const sentinelRefs = useRef<Map<string, HTMLLIElement | null>>(new Map());

  useEffect(() => {
    if (typeof window === "undefined" || typeof IntersectionObserver === "undefined") {
      setActivated(new Set(audioStems.map((s) => s.url)));
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        setActivated((prev) => {
          const next = new Set(prev);
          let changed = false;
          for (const entry of entries) {
            if (entry.isIntersecting) {
              const url = (entry.target as HTMLElement).dataset.stemUrl;
              if (url && !next.has(url)) {
                next.add(url);
                changed = true;
              }
            }
          }
          return changed ? next : prev;
        });
      },
      { rootMargin: "100px" },
    );
    for (const el of sentinelRefs.current.values()) {
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [audioStems]);

  // Stable callbacks so the memoized rows don't re-render while the
  // player ticks `currentTime` at 4 Hz. (Declared before the early return
  // below so the hook order stays unconditional.)
  const onPlayAudio = useCallback(
    (stem: StemInfo) => {
      // Compare base URLs (without query/token) so the player identity
      // remains stable across token refreshes. The backend already
      // appends a short-lived download token to stem.url.
      const currentBase = current?.url?.split("?")[0] ?? "";
      const stemBase = stem.url.split("?")[0];
      if (currentBase === stemBase && isPlaying) {
        pause();
      } else {
        play({ url: stem.url, title: stemLabel(t, stem.name), kind: "audio" });
      }
    },
    [current, isPlaying, play, pause, t],
  );

  const midiProfileLabel = useCallback(
    (profile?: string) => {
      switch (profile) {
        case "gm":
          return t("common.gmMidi");
        case "xg":
          return t("common.xgMidi");
        default:
          return t("common.rawMidi");
      }
    },
    [t],
  );

  if (audioStems.length === 0 && midiStems.length === 0) return null;

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
          {t("common.stemMixer")}
        </h2>
        <span className="text-xs text-slate-500 dark:text-slate-400">
          {audioStems.length} {t("common.audio").toLowerCase()} · {midiStems.length}{" "}
          {t("common.midi").toLowerCase()}
        </span>
      </div>
      {audioStems.length > 0 && (
        <ul className="space-y-2">
          {audioStems.map((stem) => {
            const isCurrent = (current?.url?.split("?")[0] ?? "") === stem.url.split("?")[0];
            return (
              <li
                key={stem.name}
                ref={(el) => {
                  if (el) sentinelRefs.current.set(stem.url, el);
                }}
                data-stem-url={stem.url}
              >
                {activated.has(stem.url) ? (
                  <StemRow
                    stem={stem}
                    isCurrent={isCurrent}
                    isPlaying={isCurrent && isPlaying}
                    onPlay={onPlayAudio}
                  />
                ) : (
                  <StemSkeleton stem={stem} onPlay={onPlayAudio} />
                )}
              </li>
            );
          })}
        </ul>
      )}

      {midiStems.length > 0 && (
        <details className="rounded-lg border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
          <summary className="cursor-pointer text-sm font-semibold text-slate-900 dark:text-slate-100">
            {t("common.midiStems")} ({midiStems.length})
          </summary>
          <ul className="mt-3 space-y-2">
            {midiStems.map((stem) => (
              <MidiStemRow key={stem.name} stem={stem} midiProfileLabel={midiProfileLabel} />
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
