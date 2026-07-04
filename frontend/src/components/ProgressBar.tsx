interface ProgressBarProps {
  value: number; // 0-100
  className?: string;
}

export function ProgressBar({ value, className = "" }: ProgressBarProps) {
  const v = Math.max(0, Math.min(100, value));
  // 10 cells: ░ = empty, █ = filled.
  const cells = 10;
  const filled = Math.round((v / 100) * cells);
  return (
    <div
      className={`flex items-center gap-2 ${className}`}
      role="progressbar"
      aria-valuenow={v}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label="progress"
    >
      <div className="font-mono text-xs tracking-tight text-slate-700 dark:text-slate-200" aria-hidden="true">
        {"█".repeat(filled)}
        <span className="text-slate-300 dark:text-slate-700">{"░".repeat(cells - filled)}</span>
      </div>
      <div className="text-xs tabular-nums text-slate-500 dark:text-slate-400">{v}%</div>
    </div>
  );
}
