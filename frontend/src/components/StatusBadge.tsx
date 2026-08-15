import type { AudioTaskStatus } from "@/types/audio";

const STYLES: Record<AudioTaskStatus, string> = {
  UPLOADED:
    "bg-slate-100 text-slate-700 ring-slate-200 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-700",
  PROCESSING:
    "bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:ring-amber-800",
  FINISHED:
    "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:ring-emerald-800",
  FAILED:
    "bg-red-50 text-red-700 ring-red-200 dark:bg-red-950/40 dark:text-red-300 dark:ring-red-800",
};

export function StatusBadge({ status }: { status: AudioTaskStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${STYLES[status]}`}
    >
      {status}
    </span>
  );
}
