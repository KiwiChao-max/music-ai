/**
 * Loading & empty/error state primitives shared across the app.
 *
 * Three components:
 *
 *  * `<Skeleton />`         — a single shimmering placeholder block.
 *  * `<EmptyState />`      — a card shown when a list is genuinely empty
 *                            ("you haven't uploaded anything yet").
 *  * `<ErrorState />`      — a card shown when an API call failed.
 *
 * The shared shape makes every page in the app feel consistent: same
 * padding, same icon, same button style. That's the kind of detail
 * that makes the difference between "personal project" and
 * "product".
 */
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

interface SkeletonProps {
  className?: string;
  /** Width in Tailwind units; defaults to `w-full`. */
  width?: string;
  /** Height in Tailwind units; defaults to `h-4`. */
  height?: string;
}

export function Skeleton({
  className = "",
  width = "w-full",
  height = "h-4",
}: SkeletonProps) {
  return (
    <div
      aria-hidden="true"
      className={`${width} ${height} animate-pulse rounded bg-slate-200 dark:bg-slate-800 ${className}`}
    />
  );
}

interface EmptyStateProps {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
}

export function EmptyState({
  title,
  description,
  action,
  icon,
}: EmptyStateProps) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-white p-10 text-center dark:border-slate-700 dark:bg-slate-900">
      <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500">
        {icon ?? <DefaultEmptyIcon />}
      </div>
      <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
      {description && (
        <p className="mx-auto mt-1 max-w-sm text-sm text-slate-500 dark:text-slate-400">
          {description}
        </p>
      )}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}

function DefaultEmptyIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M9 9h.01M15 9h.01M9 15c1 1 2 1.5 3 1.5s2-.5 3-1.5" />
    </svg>
  );
}

interface ErrorStateProps {
  title?: string;
  error: unknown;
  onRetry?: () => void;
}

export function ErrorState({
  title,
  error,
  onRetry,
}: ErrorStateProps) {
  const { t } = useTranslation();
  const message =
    error instanceof Error
      ? error.message
      : typeof error === "string"
        ? error
        : t("errors.generic");

  return (
    <div
      role="alert"
      className="rounded-lg border border-rose-200 bg-rose-50 p-6 dark:border-rose-800 dark:bg-rose-950/30"
    >
      <h3 className="text-base font-semibold text-rose-900 dark:text-rose-100">
        {title ?? t("errors.generic")}
      </h3>
      <p className="mt-1 break-words rounded bg-rose-100 px-2 py-1 font-mono text-xs text-rose-900 dark:bg-rose-900/40 dark:text-rose-100">
        {message}
      </p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 inline-flex items-center justify-center rounded-md bg-rose-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-rose-700 dark:bg-rose-700 dark:hover:bg-rose-600"
        >
          {t("common.retry")}
        </button>
      )}
    </div>
  );
}
