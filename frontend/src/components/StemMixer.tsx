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
import { useEffect, useRef, useState } from "react";
import WaveSurfer from "wavesurfer.js";

import { usePlayer } from "@/contexts/PlayerContext";
import type { StemInfo } from "@/types/audio";

interface StemRowProps {
  stem: StemInfo;
  isCurrent: boolean;
  isPlaying: boolean;
  onPlay: () => void;
}

function StemWaveform({ url, reloadKey }: { url: string; reloadKey: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  // Re-create the wavesurfer instance when the URL changes so we don't
  // accidentally re-use a dead player.
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
      // The per-row wave is non-interactive; clicking it would conflict
      // with the play/pause button. The global bar handles seeking.
      interact: false,
    });
    return () => {
      ws.destroy();
    };
  }, [reloadKey, url]);
  return <div ref={containerRef} className="h-9 w-full" />;
}

function StemRow({ stem, isCurrent, isPlaying, onPlay }: StemRowProps) {
  return (
    <li className="grid grid-cols-[auto_1fr_auto] items-center gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex w-32 shrink-0 flex-col gap-1">
        <span className="truncate text-sm font-semibold capitalize text-slate-900 dark:text-slate-100">
          {stem.name}
        </span>
        <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Audio
        </span>
      </div>
      <div className="min-w-0">
        <StemWaveform url={stem.url} reloadKey={stem.url} />
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <button
          type="button"
          onClick={onPlay}
          className={`inline-flex min-w-20 items-center justify-center rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
            isCurrent && isPlaying
              ? "bg-emerald-600 text-white hover:bg-emerald-700"
              : "bg-slate-900 text-white hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
          }`}
          aria-label={isCurrent && isPlaying ? `Pause ${stem.name}` : `Play ${stem.name}`}
        >
          {isCurrent && isPlaying ? "Pause" : "Play"}
        </button>
        <a
          href={stem.url}
          download={`${stem.name}.wav`}
          className="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
        >
          Download
        </a>
      </div>
    </li>
  );
}

interface StemMixerProps {
  stems: StemInfo[];
}

/**
 * Stem mixer: per-stem waveform previews wired into the global player.
 *
 * Audio stems get a row with a small waveform + play/pause. MIDI stems
 * don't have a waveform (they're symbolic data); they are listed as
 * a simple download row at the bottom for completeness.
 */
export function StemMixer({ stems }: StemMixerProps) {
  const audioStems = stems.filter((s) => s.kind === "audio");
  const midiStems = stems.filter((s) => s.kind === "midi");
  const { current, isPlaying, play, pause } = usePlayer();

  // Track which stem URL has been "activated" (scrolled into view at
  // least once). We only mount the wavesurfer for activated stems to
  // avoid N network decodes for hidden rows.
  const [activated, setActivated] = useState<Set<string>>(new Set());
  const sentinelRefs = useRef<Map<string, HTMLLIElement | null>>(new Map());

  useEffect(() => {
    if (typeof window === "undefined" || typeof IntersectionObserver === "undefined") {
      // No IntersectionObserver (test env / old browser): just activate
      // everything so the user still gets waveforms.
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

  if (audioStems.length === 0 && midiStems.length === 0) return null;

  const onPlayAudio = (stem: StemInfo) => {
    if (current?.url === stem.url && isPlaying) {
      pause();
    } else {
      play({ url: stem.url, title: stem.name, kind: "audio" });
    }
  };

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Stem Mixer</h2>
        <span className="text-xs text-slate-500 dark:text-slate-400">
          {audioStems.length} audio · {midiStems.length} midi
        </span>
      </div>
      {audioStems.length > 0 && (
        <ul className="space-y-2">
          {audioStems.map((stem) => {
            const isCurrent = current?.url === stem.url;
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
                    onPlay={() => onPlayAudio(stem)}
                  />
                ) : (
                  <div className="grid h-[68px] grid-cols-[auto_1fr_auto] items-center gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
                    <div className="w-32 shrink-0">
                      <p className="truncate text-sm font-semibold capitalize text-slate-900 dark:text-slate-100">
                        {stem.name}
                      </p>
                      <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                        Audio
                      </p>
                    </div>
                    <div className="h-9 w-full animate-pulse rounded bg-slate-100 dark:bg-slate-800" />
                    <div className="flex shrink-0 items-center gap-2">
                      <button
                        type="button"
                        onClick={() => onPlayAudio(stem)}
                        className="inline-flex min-w-20 items-center justify-center rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
                      >
                        Play
                      </button>
                      <a
                        href={stem.url}
                        download={`${stem.name}.wav`}
                        className="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
                      >
                        Download
                      </a>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {midiStems.length > 0 && (
        <details className="rounded-lg border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
          <summary className="cursor-pointer text-sm font-semibold text-slate-900 dark:text-slate-100">
            MIDI stems ({midiStems.length})
          </summary>
          <ul className="mt-3 space-y-1">
            {midiStems.map((stem) => (
              <li
                key={stem.name}
                className="flex items-center justify-between rounded border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800"
              >
                <span className="truncate text-slate-700 dark:text-slate-300">{stem.name}</span>
                <a
                  href={stem.url}
                  download={`${stem.name}.mid`}
                  className="text-xs font-semibold uppercase tracking-wide text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
                >
                  .mid
                </a>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
