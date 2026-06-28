import { usePlayer } from "@/contexts/PlayerContext";
import type { StemInfo } from "@/types/audio";

interface StemRowProps {
  stem: StemInfo;
  isPlaying: boolean;
  onPlay: () => void;
}

function downloadName(stem: StemInfo): string {
  return stem.kind === "midi" ? `${stem.name}.mid` : `${stem.name}.wav`;
}

function midiProfileLabel(stem: StemInfo): string {
  if (stem.profile === "gm") return "GM MIDI";
  if (stem.profile === "xg") return "XG MIDI";
  return "RAW MIDI";
}

function StemRow({ stem, isPlaying, onPlay }: StemRowProps) {
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
          onClick={onPlay}
          className={`inline-flex min-w-20 items-center justify-center rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
            isPlaying
              ? "bg-emerald-600 text-white hover:bg-emerald-700"
              : "bg-slate-900 text-white hover:bg-slate-800"
          }`}
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
  // Hand playback off to the global player so it survives navigation
  // and shows up in the persistent transport bar.
  const { current, isPlaying, play, pause } = usePlayer();

  const onPlay = (stem: StemInfo) => {
    if (stem.kind !== "audio") return;
    if (current?.url === stem.url && isPlaying) {
      pause();
    } else {
      play({ url: stem.url, title: stem.name, kind: "audio" });
    }
  };

  return (
    <ul className="space-y-2">
      {stems.map((stem) => {
        const isCurrent = current?.url === stem.url;
        return (
          <StemRow
            key={stem.name}
            stem={stem}
            isPlaying={isCurrent && isPlaying}
            onPlay={() => onPlay(stem)}
          />
        );
      })}
    </ul>
  );
}
