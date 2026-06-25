import { useEffect, useRef, useState } from "react";

import type { StemInfo } from "@/types/audio";

interface StemRowProps {
  stem: StemInfo;
  isPlaying: boolean;
  onTogglePlay: () => void;
}

/** One row in the stem list. Self-contained styling, no playback state. */
function StemRow({ stem, isPlaying, onTogglePlay }: StemRowProps) {
  return (
    <li className="flex items-center gap-4 rounded-lg border border-slate-200 bg-white px-4 py-3">
      <span aria-hidden className="text-2xl leading-none">
        🎧
      </span>
      <span className="min-w-0 flex-1 truncate text-base font-semibold capitalize text-slate-900">
        {stem.name}
      </span>

      <button
        type="button"
        onClick={onTogglePlay}
        className="inline-flex items-center gap-1.5 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800"
        aria-label={isPlaying ? `Pause ${stem.name}` : `Play ${stem.name}`}
      >
        <span aria-hidden>{isPlaying ? "⏸" : "▶"}</span>
        <span>{isPlaying ? "Pause" : "Play"}</span>
      </button>

      <a
        href={stem.url}
        download={`${stem.name}.wav`}
        className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
      >
        <span aria-hidden>⬇</span>
        <span>Download</span>
      </a>
    </li>
  );
}

interface StemListProps {
  stems: StemInfo[];
}

/** Stem list with a single shared `<audio>` element. Only one stem plays at a time. */
export function StemList({ stems }: StemListProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [currentName, setCurrentName] = useState<string | null>(null);

  const togglePlay = (stem: StemInfo) => {
    const a = audioRef.current;
    if (!a) return;
    if (currentName === stem.name) {
      // Same stem: pause/resume.
      if (a.paused) {
        a.play().catch(() => {
          /* autoplay can be blocked until the user clicks again */
        });
      } else {
        a.pause();
      }
    } else {
      // Different stem: stop, swap source, play.
      a.pause();
      a.src = stem.url;
      a.currentTime = 0;
      setCurrentName(stem.name);
      a.play().catch(() => {
        /* user gesture is satisfied since this is a click handler */
      });
    }
  };

  const clearCurrent = () => setCurrentName(null);

  // Stop playback if the stems list is replaced (e.g. refetch after delete).
  useEffect(() => {
    audioRef.current?.pause();
    setCurrentName(null);
  }, [stems]);

  return (
    <>
      <audio
        ref={audioRef}
        onEnded={clearCurrent}
        onPause={clearCurrent}
        preload="none"
        className="hidden"
      />
      <ul className="space-y-2">
        {stems.map((s) => (
          <StemRow
            key={s.name}
            stem={s}
            isPlaying={currentName === s.name}
            onTogglePlay={() => togglePlay(s)}
          />
        ))}
      </ul>
    </>
  );
}
