/**
 * ErrorBoundary — catch-all for render-phase errors.
 *
 * Without this, a single buggy child (e.g. a malformed stem URL that
 * crashes the audio element on first paint) takes the whole SPA down
 * to a blank white page. With it, the user sees a clear "something
 * went wrong" card with a button to retry, and the rest of the app
 * keeps working.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Optional override; useful for nested sections that should fail soft. */
  fallback?: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // In a real product this would feed an observability backend
    // (Sentry / Datadog). For now, log to the console so the
    // developer can see it in DevTools.
    // eslint-disable-next-line no-console
    console.error("ErrorBoundary caught:", error, info.componentStack);
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  override render(): ReactNode {
    if (this.state.error) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div
          role="alert"
          className="mx-auto my-12 max-w-md rounded-lg border border-rose-200 bg-rose-50 p-6 text-center"
        >
          <h2 className="text-lg font-semibold text-rose-900">
            Something went wrong
          </h2>
          <p className="mt-2 text-sm text-rose-800">
            A page component failed to render. The rest of the app should
            still work — try reloading this view.
          </p>
          <p className="mt-3 break-words rounded bg-rose-100 px-2 py-1 text-left font-mono text-xs text-rose-900">
            {this.state.error.message}
          </p>
          <button
            type="button"
            onClick={this.reset}
            className="mt-4 inline-flex items-center justify-center rounded-md bg-rose-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-rose-700"
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
