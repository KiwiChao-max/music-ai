import type { AudioTaskStatus } from "@/types/audio";

const STYLES: Record<AudioTaskStatus, string> = {
  UPLOADED: "bg-slate-100 text-slate-700 ring-slate-200",
  PROCESSING: "bg-amber-50 text-amber-700 ring-amber-200",
  FINISHED: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  FAILED: "bg-red-50 text-red-700 ring-red-200",
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
