/**
 * Persistent transport bar shown at the bottom of the app.
 *
 * Renders a single wavesurfer.js waveform for the current track plus
 * play/pause, seek, time, volume, and a close button. Lives inside
 * `<MainLayout>` so it stays mounted across page navigations — that's
 * the whole point of having a global player.
 *
 * The component renders `null` when nothing is loaded. The bar slides
 * in via a CSS transition when the first track starts.
 */
import { useEffect, useRef } from "react";
import WaveSurfer from "wavesurfer.js";

import { formatTime, usePlayer } from "@/contexts/PlayerContext";

/**
 * Render a compact wavesurfer waveform for a single audio URL.
 *
 * `onSeek` is called (with the new time in seconds) when the user clicks
 * or drags on the waveform. The component doesn't own playback — it
 * only visualises and reports clicks. The parent decides what to do
 * with the seek (typically: forward to the global player).
 */
interface WaveformProps {
  url: string;
  height?: number;
  /** Reactive seed that forces the waveform to reload when it changes. */
  reloadKey: string | number;
  onReady?: (durationSec: number) => void;
  onSeek?: (timeSec: number) => void;
  className?: string;
}

function Waveform({
  url,
  height = 48,
  reloadKey,
  onReady,
  onSeek,
  className = "",
}: WaveformProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const wsRef = useRef<WaveSurfer | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;

    // Pick colors that contrast with the current theme. We re-read on
    // every mount because the user can flip the theme while the bar is
    // open.
    const dark = document.documentElement.classList.contains("dark");
    const waveColor = dark ? "#475569" : "#cbd5e1";
    const progressColor = dark ? "#e2e8f0" : "#0f172a";
    const cursorColor = dark ? "#e2e8f0" : "#0f172a";

    const ws = WaveSurfer.create({
      container,
      url,
      height,
      // Visual: a thin bar with rounded edges. We keep the cursor and
      // progress visible so the user can see where playback is.
      waveColor,
      progressColor,
      cursorColor,
      cursorWidth: 1,
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      normalize: true,
      // Keep the wave from auto-playing; the global player drives
      // playback separately and the bar reflects that.
      autoplay: false,
      interact: true,
    });
    wsRef.current = ws;

    const handleReady = () => {
      if (onReady) onReady(ws.getDuration());
    };
    const handleInteraction = (newTime: number) => {
      if (onSeek) onSeek(newTime);
    };
    ws.on("ready", handleReady);
    ws.on("interaction", handleInteraction);

    return () => {
      ws.un("ready", handleReady);
      ws.un("interaction", handleInteraction);
      ws.destroy();
      wsRef.current = null;
    };
    // `reloadKey` is the only thing that should ever cause a remount.
    // `url` and the callbacks are intentionally captured in the closure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey]);

  return <div ref={containerRef} className={className} />;
}

export function PlayerBar() {
  const {
    current,
    isPlaying,
    currentTime,
    duration,
    volume,
    loading,
    toggle,
    stop,
    seek,
    setVolume,
  } = usePlayer();

  if (!current) return null;

  return (
    <div
      role="region"
      aria-label="Audio player"
      className="sticky bottom-0 left-0 right-0 z-30 border-t border-slate-200 bg-white shadow-[0_-4px_12px_rgba(15,23,42,0.06)] dark:border-slate-800 dark:bg-slate-900 dark:shadow-[0_-4px_12px_rgba(0,0,0,0.4)]"
    >
      <div className="mx-auto flex w-full max-w-5xl items-center gap-4 px-6 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              className="inline-flex h-2 w-2 shrink-0 rounded-full"
              style={{ backgroundColor: loading ? "#f59e0b" : isPlaying ? "#10b981" : "#94a3b8" }}
              aria-hidden="true"
            />
            <p className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
              {current.title}
              {current.kind && (
                <span className="ml-2 inline-flex items-center rounded bg-slate-100 px-1.5 py-0.5 align-middle text-[10px] font-semibold uppercase tracking-wide text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                  {current.kind}
                </span>
              )}
            </p>
            <span className="shrink-0 font-mono text-xs text-slate-500 dark:text-slate-400">
              {formatTime(currentTime)} / {formatTime(duration)}
            </span>
          </div>
          <div className="mt-1.5">
            <Waveform
              url={current.url}
              reloadKey={current.url}
              onSeek={seek}
              className="w-full"
            />
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={toggle}
            disabled={loading}
            className="inline-flex h-10 w-10 items-center justify-center rounded-full bg-slate-900 text-white transition-colors hover:bg-slate-800 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-200"
            aria-label={isPlaying ? "Pause" : "Play"}
          >
            {isPlaying ? (
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="currentColor"
                aria-hidden="true"
              >
                <rect x="6" y="5" width="4" height="14" rx="1" />
                <rect x="14" y="5" width="4" height="14" rx="1" />
              </svg>
            ) : (
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="currentColor"
                aria-hidden="true"
              >
                <path d="M8 5v14l11-7z" />
              </svg>
            )}
          </button>

          <label className="flex items-center gap-1 text-xs text-slate-500 dark:text-slate-400" title="Volume">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
              <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" />
            </svg>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={volume}
              onChange={(e) => setVolume(Number(e.target.value))}
              className="h-1 w-20 cursor-pointer accent-slate-900 dark:accent-slate-100"
              aria-label="Volume"
            />
          </label>

          <button
            type="button"
            onClick={stop}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
            aria-label="Close player"
            title="Close player"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}
