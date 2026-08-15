import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, useCallback } from "react";

// ---------------------------------------------------------------------------
// Mock i18n
// ---------------------------------------------------------------------------
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      const defaults: Record<string, string> = {
        "detail.delete": "Delete",
        "detail.deleting": "Deleting...",
        "detail.deleteConfirm": "Delete task #{{id}} ({{name}})?",
        "detail.deleteError": "Delete failed",
      };
      let text = defaults[key] ?? key;
      if (opts) {
        text = text.replace(/\{\{(\w+)\}\}/g, (_, k) => String(opts[k] ?? ""));
      }
      return text;
    },
    i18n: { language: "en" },
  }),
}));

// ---------------------------------------------------------------------------
// Mock delete API
// ---------------------------------------------------------------------------
const mockDeleteApi = vi.fn();

vi.mock("@/api/audio", () => ({
  audioApi: { delete: (...args: unknown[]) => mockDeleteApi(...args) },
}));

// ---------------------------------------------------------------------------
// Test component: isolated delete button with confirmation
// ---------------------------------------------------------------------------
function DeleteButton({ taskId, taskName }: { taskId: number; taskName: string }) {
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<Error | null>(null);

  const handleDelete = useCallback(() => {
    if (!window.confirm(`Delete ${taskName} (ID: ${taskId})?`)) return;
    setIsDeleting(true);
    setDeleteError(null);
    mockDeleteApi(taskId)
      .then(() => {
        window.location.href = "/audio";
      })
      .catch((err: Error) => {
        setIsDeleting(false);
        setDeleteError(err);
      });
  }, [taskId, taskName]);

  return (
    <div>
      {deleteError && <div role="alert">{deleteError.message}</div>}
      <button onClick={handleDelete} disabled={isDeleting}>
        {isDeleting ? "Deleting..." : "Delete"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------
function renderDeleteButton(taskId = 42, taskName = "my-song.wav") {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <DeleteButton taskId={taskId} taskName={taskName} />
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("Delete confirmation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the delete button", () => {
    renderDeleteButton();
    expect(screen.getByRole("button", { name: /delete/i })).toBeInTheDocument();
  });

  it("shows window.confirm when delete is clicked", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderDeleteButton();
    await userEvent.click(screen.getByRole("button", { name: /delete/i }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("my-song.wav"));
    confirmSpy.mockRestore();
  });

  it("does not call delete API when user cancels confirm", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderDeleteButton();
    await userEvent.click(screen.getByRole("button", { name: /delete/i }));
    expect(mockDeleteApi).not.toHaveBeenCalled();
  });

  it("calls delete API when user confirms", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mockDeleteApi.mockResolvedValue(undefined);
    renderDeleteButton();
    await userEvent.click(screen.getByRole("button", { name: /delete/i }));
    expect(mockDeleteApi).toHaveBeenCalledWith(42);
  });

  it("disables the button and shows 'Deleting...' while pending", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    // Make the API call never resolve (simulating pending state)
    mockDeleteApi.mockReturnValue(new Promise(() => {}));
    renderDeleteButton();
    await userEvent.click(screen.getByRole("button", { name: /delete/i }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /deleting/i })).toBeDisabled();
    });
  });

  it("shows error state when delete fails", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mockDeleteApi.mockRejectedValue(new Error("Permission denied"));
    renderDeleteButton();
    await userEvent.click(screen.getByRole("button", { name: /delete/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByText("Permission denied")).toBeInTheDocument();
    // Button should be re-enabled after error
    expect(screen.getByRole("button", { name: /delete/i })).toBeEnabled();
  });
});
