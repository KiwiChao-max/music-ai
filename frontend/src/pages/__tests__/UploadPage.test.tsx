import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { UploadPage } from "@/pages/UploadPage";

// ---------------------------------------------------------------------------
// Mock i18n
// ---------------------------------------------------------------------------
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      const defaults: Record<string, string> = {
        "upload.title": "Upload Audio",
        "upload.subtitle": "Choose a file to analyze",
        "upload.dropLabel": "Drop your audio file here",
        "upload.dropHelp": "Max {{max}} MB",
        "upload.remove": "Remove",
        "upload.clear": "Clear",
        "upload.submit": "Upload",
        "upload.submitting": "Uploading...",
        "upload.unknownType": "Unknown",
        "upload.error.title": "Upload failed",
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
// Mock upload hook
// ---------------------------------------------------------------------------
const mockUploadMutate = vi.fn();
const mockUseUploadAudio = vi.fn(() => ({
  mutate: mockUploadMutate,
  isPending: false,
  isError: false,
  error: null,
  reset: vi.fn(),
}));

vi.mock("@/hooks/useAudioTasks", () => ({
  useUploadAudio: () => mockUseUploadAudio(),
  useAudioTask: vi.fn(),
  useAudioTasks: vi.fn(),
  useStartProcess: vi.fn(),
  useDeleteAudio: vi.fn(),
  useStems: vi.fn(),
  useMusicAnalysis: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Mock upload utils
// ---------------------------------------------------------------------------
vi.mock("@/utils/upload", () => ({
  MAX_UPLOAD_BYTES: 200 * 1024 * 1024,
  validateAudioFile: (file: File | null) => {
    if (!file) return null;
    if (file.size > 200 * 1024 * 1024) return "Audio files must be 200 MB or smaller.";
    return null;
  },
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function makeFile(name: string, size = 1024, type = "audio/wav"): File {
  return new File([new Uint8Array(size)], name, { type });
}

function renderUploadPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <UploadPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("UploadPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the upload form with heading and drop zone", () => {
    renderUploadPage();
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(screen.getByLabelText(/drop/i)).toBeInTheDocument();
  });

  it("submit button is disabled when no file is selected", () => {
    renderUploadPage();
    const submitBtn = screen.getByRole("button", { name: /upload/i });
    expect(submitBtn).toBeDisabled();
  });

  it("enables submit button after selecting a file", async () => {
    renderUploadPage();
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = makeFile("test.wav");
    await userEvent.upload(input, file);
    const submitBtn = screen.getByRole("button", { name: /upload/i });
    expect(submitBtn).toBeEnabled();
  });

  it("shows the selected file name after picking", async () => {
    renderUploadPage();
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, makeFile("my-song.mp3"));
    expect(screen.getByText("my-song.mp3")).toBeInTheDocument();
  });

  it("removes the selected file when clicking Remove", async () => {
    renderUploadPage();
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, makeFile("song.wav"));
    expect(screen.getByText("song.wav")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /remove/i }));
    expect(screen.queryByText("song.wav")).not.toBeInTheDocument();
  });

  it("calls upload mutation on submit", async () => {
    renderUploadPage();
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, makeFile("submit.wav"));
    await userEvent.click(screen.getByRole("button", { name: /upload/i }));
    expect(mockUploadMutate).toHaveBeenCalledTimes(1);
    const passedFile = mockUploadMutate.mock.calls[0][0] as File;
    expect(passedFile.name).toBe("submit.wav");
  });

  it("disables submit button while uploading", async () => {
    mockUseUploadAudio.mockReturnValue({
      mutate: mockUploadMutate,
      isPending: true,
      isError: false,
      error: null,
      reset: vi.fn(),
    });
    renderUploadPage();
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, makeFile("song.wav"));
    const submitBtn = screen.getByRole("button", { name: /uploading/i });
    expect(submitBtn).toBeDisabled();
  });

  it("shows error state when upload fails", async () => {
    mockUseUploadAudio.mockReturnValue({
      mutate: mockUploadMutate,
      isPending: false,
      isError: true,
      error: new Error("Network error"),
      reset: vi.fn(),
    });
    renderUploadPage();
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Network error")).toBeInTheDocument();
  });
});