import { useEffect, useRef, useState } from "react";

import type { StemInfo } from "@/types/audio";

interface StemRowProps {
  stem: StemInfo;
  isPlaying: boolean;
  onTogglePlay: () => void;
}

function downloadName(stem: StemInfo): string {
  return stem.kind === "midi" ? `${stem.name}.mid` : `${stem.name}.wav`;
}

function midiProfileLabel(stem: StemInfo): string {
  if (stem.profile === "gm") return "GM MIDI";
  if (stem.profile === "xg") return "XG MIDI";
  return "RAW MIDI";
}

function StemRow({ stem, isPlaying, onTogglePlay }: StemRowProps) {
  const isMidi = stem.kind === "midi";

  return (
    <li className="flex items-center gap-4 rounded-lg border border-slate-200 bg-white px-4 py-3">
      <span className="w-12 shrink-0 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {isMidi ? "MIDI" : "Audio"}
      </span>
      <span className="min-w-0 flex-1 truncate text-base font-semibold capitalize text-slate-900">
        {stem.name}
        {isMidi && (
          <span className="ml-2 inline-flex items-center rounded bg-indigo-100 px-1.5 py-0.5 align-middle text-[10px] font-semibold uppercase tracking-wide text-indigo-700">
            {midiProfileLabel(stem)}
          </span>
        )}
      </span>

      {!isMidi && (
        <button
          type="button"
          onClick={onTogglePlay}
          className="inline-flex min-w-20 items-center justify-center rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800"
          aria-label={isPlaying ? `Pause ${stem.name}` : `Play ${stem.name}`}
        >
          {isPlaying ? "Pause" : "Play"}
        </button>
      )}

      <a
        href={stem.url}
        download={downloadName(stem)}
        className="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
      >
        Download
      </a>
    </li>
  );
}

interface StemListProps {
  stems: StemInfo[];
}

export function StemList({ stems }: StemListProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // `currentName` = which stem is loaded. `paused` reflects the actual
  // play/pause state of the <audio> element. Keeping these as two separate
  // state values lets us render the right button label for every transition
  // (play → pause → play again) without the previous "Play flash" bug that
  // happened when `onPause` itself cleared the active stem.
  const [currentName, setCurrentName] = useState<string | null>(null);
  const [paused, setPaused] = useState(true);

  const togglePlay = (stem: StemInfo) => {
    if (stem.kind !== "audio") return;
    const audio = audioRef.current;
    if (!audio) return;

    if (currentName === stem.name) {
      if (audio.paused) {
        audio.play().catch(() => {
          // Autoplay can be blocked until the user clicks again.
        });
      } else {
        audio.pause();
      }
      return;
    }

    // Switching to a different stem. Pause the current one, set the new
    // stem *first* (so React state is in sync before `pause()` triggers an
    // async onPause event), then start the new playback.
    audio.pause();
    setCurrentName(stem.name);
    setPaused(false);
    audio.src = stem.url;
    audio.currentTime = 0;
    audio.play().catch(() => {
      // User gesture is satisfied since this is a click handler.
    });
  };

  useEffect(() => {
    audioRef.current?.pause();
    setCurrentName(null);
    setPaused(true);
  }, [stems]);

  return (
    <>
      <audio
        ref={audioRef}
        onPlay={() => setPaused(false)}
        onPause={() => setPaused(true)}
        onEnded={() => {
          setPaused(true);
          setCurrentName(null);
        }}
        preload="none"
        className="hidden"
      />
      <ul className="space-y-2">
        {stems.map((stem) => {
          const isCurrent = currentName === stem.name;
          return (
            <StemRow
              key={stem.name}
              stem={stem}
              isPlaying={isCurrent && !paused}
              onTogglePlay={() => togglePlay(stem)}
            />
          );
        })}
      </ul>
    </>
  );
}
