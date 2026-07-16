import { useTranslation } from "react-i18next";

import type { StemInfo } from "@/types/audio";

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

export function DrumPartPanel({ stems }: { stems: StemInfo[] }) {
  const { t } = useTranslation();
  const parts = stems
    .filter((s) => s.kind === "midi" && s.name.startsWith("drums_"))
    .map((s) => {
      const part = s.name.replace(/^drums_/, "");
      return { part, stem: s };
    })
    .filter((entry) =>
      DRUM_PARTS.includes(entry.part as (typeof DRUM_PARTS)[number]),
    )
    .sort(
      (a, b) =>
        DRUM_PARTS.indexOf(a.part as (typeof DRUM_PARTS)[number]) -
        DRUM_PARTS.indexOf(b.part as (typeof DRUM_PARTS)[number]),
    );
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