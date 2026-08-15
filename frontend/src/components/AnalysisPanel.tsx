import { useTranslation } from "react-i18next";

import type { DetectedInstrument, MusicAnalysis, SoundfontOverride } from "@/types/audio";

function formatRange(start: number, end: number): string {
  return `${start.toFixed(1)}s-${end.toFixed(1)}s`;
}

function confidenceLabel(value: number, t: (key: string) => string): string {
  if (value >= 0.66) return t("detail.analysis.confidenceHigh");
  if (value >= 0.33) return t("detail.analysis.confidenceMedium");
  return t("detail.analysis.confidenceLow");
}

function formatInstrumentName(name: string): string {
  return name
    .split("_")
    .map((token) => token.charAt(0).toUpperCase() + token.slice(1))
    .join(" ");
}

function parseI18n(
  item: string,
  t: (key: string, params?: Record<string, string>) => string,
): string {
  if (!item.startsWith("$")) return item;
  const parts = item.slice(1).split("||");
  const key = `analysis.${parts[0]}`;
  if (parts.length === 1) return t(key);
  const params: Record<string, string> = {};
  parts.slice(1).forEach((p, i) => {
    params[`p${i + 1}`] = p;
  });
  return t(key, params);
}

// ---------------------------------------------------------------------------
// Sub-panels
// ---------------------------------------------------------------------------

function AdviceList({ title, items }: { title: string; items: string[] }) {
  const { t } = useTranslation();
  if (items.length === 0) return null;
  return (
    <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4">
      <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-700 dark:text-slate-300">
        {items.map((item) => (
          <li key={item}>{parseI18n(item, t)}</li>
        ))}
      </ul>
    </div>
  );
}

function DetectedInstrumentsPanel({
  items,
  dominant,
}: {
  items: DetectedInstrument[];
  dominant: string | null;
}) {
  const { t } = useTranslation();
  const sorted = [...items].sort((a, b) => b.probability - a.probability);
  return (
    <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          {t("detail.analysis.instruments")}
        </h3>
        {dominant && (
          <span className="rounded bg-indigo-100 dark:bg-indigo-900/40 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-indigo-700 dark:text-indigo-300">
            {t("detail.analysis.dominant", {
              name: formatInstrumentName(dominant),
            })}
          </span>
        )}
      </div>
      <ul className="mt-3 space-y-2">
        {sorted.map((item) => (
          <li key={item.instrument} className="space-y-1">
            <div className="flex items-center justify-between text-xs text-slate-600 dark:text-slate-400">
              <span className="font-medium text-slate-700 dark:text-slate-300">
                {formatInstrumentName(item.instrument)}
              </span>
              <span className="font-mono">{(item.probability * 100).toFixed(0)}%</span>
            </div>
            <div
              className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800"
              role="progressbar"
              aria-valuenow={Math.round(item.probability * 100)}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={formatInstrumentName(item.instrument)}
            >
              <div
                className="h-full bg-slate-700 dark:bg-slate-400"
                style={{ width: `${Math.max(2, item.probability * 100)}%` }}
                aria-hidden="true"
              />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SoundfontOverridesPanel({ overrides }: { overrides: SoundfontOverride[] }) {
  const { t } = useTranslation();
  return (
    <div className="rounded-lg border border-indigo-200 dark:border-indigo-800 bg-indigo-50/50 dark:bg-indigo-950/20 p-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          {t("detail.soundfont.title")}
        </h3>
        <span className="rounded bg-indigo-100 dark:bg-indigo-900/40 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-indigo-700 dark:text-indigo-300">
          {t("detail.soundfont.count", { count: overrides.length })}
        </span>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        {t("detail.soundfont.subtitle")}
      </p>
      <ul className="mt-3 space-y-2">
        {overrides.map((ov) => (
          <li
            key={`${ov.stem}-${ov.program}-${ov.bank_msb}-${ov.bank_lsb}`}
            className="flex items-center justify-between gap-3 rounded border border-indigo-200 dark:border-indigo-800 bg-white dark:bg-slate-900 px-3 py-2"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">
                {ov.label}
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {t("detail.soundfont.stem", { stem: ov.stem })} ·{" "}
                {t("detail.soundfont.bank", {
                  msb: ov.bank_msb,
                  lsb: ov.bank_lsb,
                  program: ov.program,
                })}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main exported panel
// ---------------------------------------------------------------------------

export function AnalysisPanel({ analysis }: { analysis: MusicAnalysis }) {
  const { t } = useTranslation();
  const keyLabel =
    analysis.key && analysis.scale
      ? `${analysis.key} ${analysis.scale}`
      : t("detail.analysis.unknown");

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
          {t("detail.analysis.title")}
        </h2>
        {analysis.warnings.length > 0 && (
          <span className="rounded bg-amber-100 dark:bg-amber-900/40 px-2 py-1 text-xs font-medium text-amber-800 dark:text-amber-200">
            {t(
              analysis.warnings.length === 1
                ? "detail.analysis.warning"
                : "detail.analysis.warningPlural",
              { count: analysis.warnings.length },
            )}
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {t("detail.analysis.bpm")}
          </p>
          <p className="mt-1 text-2xl font-semibold text-slate-900 dark:text-slate-100">
            {analysis.bpm ?? "-"}
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {confidenceLabel(analysis.bpm_confidence, t)}
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {t("detail.analysis.key")}
          </p>
          <p className="mt-1 text-lg font-semibold text-slate-900 dark:text-slate-100">
            {keyLabel}
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {confidenceLabel(analysis.key_confidence, t)}
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {t("detail.analysis.notes")}
          </p>
          <p className="mt-1 text-2xl font-semibold text-slate-900 dark:text-slate-100">
            {analysis.note_count}
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {analysis.pitch_range ?? "-"}
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {t("detail.analysis.duration")}
          </p>
          <p className="mt-1 text-2xl font-semibold text-slate-900 dark:text-slate-100">
            {analysis.duration.toFixed(1)}s
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {t("detail.analysis.analysisWindow")}
          </p>
        </div>
      </div>

      {analysis.chords.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            {t("detail.analysis.chordMap")}
          </h3>
          <div className="mt-3 flex flex-wrap gap-2">
            {analysis.chords.map((chord) => (
              <span
                key={`${chord.start}-${chord.end}-${chord.chord}`}
                className="rounded border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/40 px-2.5 py-1.5 text-sm text-slate-700 dark:text-slate-300"
                title={t("detail.analysis.confidencePercent", {
                  value: Math.round(chord.confidence * 100),
                })}
              >
                <span className="font-semibold text-slate-900 dark:text-slate-100">
                  {chord.chord}
                </span>{" "}
                <span className="text-xs text-slate-500 dark:text-slate-400">
                  {formatRange(chord.start, chord.end)}
                </span>
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

      {(analysis.soundfont_overrides?.length ?? 0) > 0 && (
        <SoundfontOverridesPanel overrides={analysis.soundfont_overrides ?? []} />
      )}

      {analysis.sections.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 p-4">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            {t("detail.analysis.sections")}
          </h3>
          <div className="mt-3 space-y-2">
            {analysis.sections.map((section) => (
              <div
                key={section.label}
                className="rounded border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/40 p-3"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="font-semibold text-slate-900 dark:text-slate-100">
                    {t("detail.analysis.section", { label: section.label })}
                  </span>
                  <span className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    {section.energy}
                  </span>
                </div>
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                  {formatRange(section.start, section.end)} ·{" "}
                  {t("detail.analysis.density", { value: section.density })}
                </p>
                <p className="mt-2 text-sm text-slate-700 dark:text-slate-300">
                  {parseI18n(section.suggestion, t)}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        <AdviceList
          title={t("detail.analysis.instrumentSuggestions")}
          items={analysis.instrumentation}
        />
        <AdviceList
          title={t("detail.analysis.arrangementSuggestions")}
          items={analysis.arrangement}
        />
      </div>

      {analysis.warnings.length > 0 && (
        <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-4">
          <h3 className="text-sm font-semibold text-amber-900 dark:text-amber-200">
            {t("detail.analysis.analysisNotes")}
          </h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-amber-900 dark:text-amber-200">
            {analysis.warnings.map((warning) => (
              <li key={warning}>{parseI18n(warning, t)}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
