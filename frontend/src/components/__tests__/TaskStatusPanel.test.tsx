import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ProgressBar } from "@/components/ProgressBar";
import { TaskStatusPanel } from "@/components/TaskStatusPanel";
import type { AudioTask } from "@/types/audio";

// ---------------------------------------------------------------------------
// Mock i18n
// ---------------------------------------------------------------------------
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const defaults: Record<string, string> = {
        "detail.readyToProcess": "Your file is ready for processing.",
        "detail.startProcessing": "Start Processing",
        "detail.starting": "Starting...",
        "detail.startError": "Failed to start",
        "detail.processing": "Processing",
        "detail.live": "Live",
        "detail.processingFailed": "Processing failed",
        "detail.retry": "Retry",
        "detail.retrying": "Retrying...",
        "detail.retryError": "Retry failed",
        "detail.status": "Status",
        "detail.finished": "Finished",
      };
      return defaults[key] ?? key;
    },
    i18n: { language: "en" },
  }),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function makeTask(
  overrides: Partial<AudioTask> = {},
): AudioTask {
  return {
    id: 1,
    filename: "test.wav",
    status: "UPLOADED",
    progress: 0,
    current_step: null,
    duration: null,
    output_dir: null,
    error_message: null,
    finished_at: null,
    ...overrides,
  };
}

function renderTaskStatusPanel(task: AudioTask, props = {}) {
  const defaults = {
    onStart: vi.fn(),
    isStarting: false,
    startError: null,
    startRetry: vi.fn(),
    ...props,
  };
  return render(<TaskStatusPanel task={task} {...defaults} />);
}

// ---------------------------------------------------------------------------
// ProgressBar tests
// ---------------------------------------------------------------------------
describe("ProgressBar", () => {
  it("renders with aria attributes for accessibility", () => {
    render(<ProgressBar value={50} />);
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "50");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
    expect(bar).toHaveAttribute("aria-label", "progress");
  });

  it("clamps value to 0-100 range", () => {
    render(<ProgressBar value={150} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    expect(screen.getByText("100%")).toBeInTheDocument();
  });

  it("shows 0% for negative values", () => {
    render(<ProgressBar value={-10} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "0");
    expect(screen.getByText("0%")).toBeInTheDocument();
  });

  it("renders filled and empty blocks proportional to value", () => {
    render(<ProgressBar value={30} />);
    // 30% of 10 cells = 3 filled, 7 empty
    const bar = screen.getByRole("progressbar");
    // The filled blocks use █, empty use ░
    const text = bar.textContent ?? "";
    // 3 filled (█) + 7 empty (░) + "30%"
    expect(text).toMatch(/█{3}/);
    expect(text).toMatch(/░{7}/);
    expect(text).toContain("30%");
  });

  it("accepts a custom className", () => {
    const { container } = render(<ProgressBar value={0} className="custom" />);
    expect(container.firstChild).toHaveClass("custom");
  });
});

// ---------------------------------------------------------------------------
// TaskStatusPanel tests
// ---------------------------------------------------------------------------
describe("TaskStatusPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("UPLOADED status", () => {
    it("shows the 'Start Processing' button", () => {
      renderTaskStatusPanel(makeTask({ status: "UPLOADED" }));
      expect(
        screen.getByRole("button", { name: /start processing/i }),
      ).toBeInTheDocument();
    });

    it("disables the button while starting", () => {
      renderTaskStatusPanel(makeTask({ status: "UPLOADED" }), {
        isStarting: true,
      });
      expect(
        screen.getByRole("button", { name: /starting/i }),
      ).toBeDisabled();
    });

    it("calls onStart when button is clicked", async () => {
      const onStart = vi.fn();
      renderTaskStatusPanel(makeTask({ status: "UPLOADED" }), { onStart });
      await userEvent.click(
        screen.getByRole("button", { name: /start processing/i }),
      );
      expect(onStart).toHaveBeenCalledTimes(1);
    });

    it("shows error when startError is provided", () => {
      renderTaskStatusPanel(makeTask({ status: "UPLOADED" }), {
        startError: new Error("broker connection failed"),
      });
      expect(screen.getByRole("alert")).toBeInTheDocument();
      expect(
        screen.getByText("broker connection failed"),
      ).toBeInTheDocument();
    });
  });

  describe("PROCESSING status", () => {
    it("shows progress bar and current step", () => {
      renderTaskStatusPanel(
        makeTask({
          status: "PROCESSING",
          progress: 60,
          current_step: "Separating stems...",
        }),
      );
      expect(screen.getByRole("progressbar")).toBeInTheDocument();
      expect(screen.getByText("60%")).toBeInTheDocument();
      expect(screen.getByText("Separating stems...")).toBeInTheDocument();
    });

    it("shows default step text when current_step is null", () => {
      renderTaskStatusPanel(
        makeTask({ status: "PROCESSING", progress: 10, current_step: null }),
      );
      expect(screen.getByText("Starting...")).toBeInTheDocument();
    });
  });

  describe("FAILED status", () => {
    it("shows error message and retry button", () => {
      renderTaskStatusPanel(
        makeTask({
          status: "FAILED",
          error_message: "Demucs model not found",
        }),
      );
      expect(screen.getByText(/processing failed/i)).toBeInTheDocument();
      expect(screen.getByText("Demucs model not found")).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: /retry/i }),
      ).toBeInTheDocument();
    });

    it("calls onStart when retry is clicked", async () => {
      const onStart = vi.fn();
      renderTaskStatusPanel(
        makeTask({ status: "FAILED", error_message: "timeout" }),
        { onStart },
      );
      await userEvent.click(screen.getByRole("button", { name: /retry/i }));
      expect(onStart).toHaveBeenCalledTimes(1);
    });
  });

  describe("FINISHED status", () => {
    it("shows the finished status indicator", () => {
      renderTaskStatusPanel(
        makeTask({ status: "FINISHED", progress: 100 }),
      );
      expect(screen.getByText("Finished")).toBeInTheDocument();
    });
  });
});