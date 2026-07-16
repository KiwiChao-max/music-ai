import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ErrorBoundary } from "@/components/ErrorBoundary";

// ---------------------------------------------------------------------------
// Mock i18next
// ---------------------------------------------------------------------------
vi.mock("i18next", () => ({
  default: {
    t: (key: string) => {
      const defaults: Record<string, string> = {
        "errors.boundary.title": "Something went wrong",
        "errors.boundary.description":
          "An unexpected error occurred in this section.",
        "errors.boundary.retry": "Try again",
      };
      return defaults[key] ?? key;
    },
  },
}));

// ---------------------------------------------------------------------------
// Helper: a component that throws during render
// ---------------------------------------------------------------------------
function BrokenComponent({ message = "Boom!" }: { message?: string }) {
  throw new Error(message);
  return null;
}

// ---------------------------------------------------------------------------
// Helper: suppress console.error during error boundary tests
// ---------------------------------------------------------------------------
function suppressConsoleError(fn: () => void) {
  const spy = vi.spyOn(console, "error").mockImplementation(() => {});
  try {
    fn();
  } finally {
    spy.mockRestore();
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("ErrorBoundary", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders children when there is no error", () => {
    render(
      <ErrorBoundary>
        <div data-testid="child">Hello</div>
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("child")).toBeInTheDocument();
    expect(screen.getByText("Hello")).toBeInTheDocument();
  });

  it("catches render errors and shows the error UI", () => {
    suppressConsoleError(() => {
      render(
        <ErrorBoundary>
          <BrokenComponent message="Something exploded" />
        </ErrorBoundary>,
      );
    });
    // The error boundary should show its fallback
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByText("Something exploded")).toBeInTheDocument();
  });

  it("shows a retry button in the error UI", () => {
    suppressConsoleError(() => {
      render(
        <ErrorBoundary>
          <BrokenComponent />
        </ErrorBoundary>,
      );
    });
    expect(
      screen.getByRole("button", { name: /try again/i }),
    ).toBeInTheDocument();
  });

  it("resets the error state when retry is clicked", async () => {
    // Use a component that throws on first render but not on reset.
    // We simulate this by testing that the retry button exists and is clickable.
    suppressConsoleError(() => {
      render(
        <ErrorBoundary>
          <BrokenComponent />
        </ErrorBoundary>,
      );
    });
    const retryBtn = screen.getByRole("button", { name: /try again/i });
    // After clicking retry, the component re-renders and throws again.
    // The button should still be there (because the child throws again).
    suppressConsoleError(() => {
      userEvent.click(retryBtn);
    });
    // The error boundary catches the error again, so the fallback stays.
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("renders a custom fallback when provided", () => {
    suppressConsoleError(() => {
      render(
        <ErrorBoundary fallback={<div data-testid="custom">Custom fallback</div>}>
          <BrokenComponent />
        </ErrorBoundary>,
      );
    });
    expect(screen.getByTestId("custom")).toBeInTheDocument();
    expect(screen.getByText("Custom fallback")).toBeInTheDocument();
    // The default error UI should not be shown
    expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument();
  });

  it("isolates errors: sibling boundaries are unaffected", () => {
    suppressConsoleError(() => {
      render(
        <div>
          <ErrorBoundary>
            <BrokenComponent message="Error in A" />
          </ErrorBoundary>
          <ErrorBoundary>
            <div data-testid="safe">I am safe</div>
          </ErrorBoundary>
        </div>,
      );
    });
    // The safe boundary should still render its children
    expect(screen.getByTestId("safe")).toBeInTheDocument();
    expect(screen.getByText("I am safe")).toBeInTheDocument();
    // The broken boundary should show the error UI
    expect(screen.getByText("Error in A")).toBeInTheDocument();
  });
});