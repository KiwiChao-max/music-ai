import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "../StatusBadge";
import type { AudioTaskStatus } from "@/types/audio";

describe("StatusBadge", () => {
  const statuses: AudioTaskStatus[] = ["UPLOADED", "PROCESSING", "FINISHED", "FAILED"];

  it.each(statuses)("renders badge for status '%s'", (status) => {
    render(<StatusBadge status={status} />);
    const el = screen.getByText(status);
    expect(el).toBeInTheDocument();
    expect(el.tagName).toBe("SPAN");
  });

  it("applies correct CSS ring and color classes per status", () => {
    const { rerender } = render(<StatusBadge status="UPLOADED" />);
    expect(screen.getByText("UPLOADED").className).toContain("bg-slate-100");

    rerender(<StatusBadge status="PROCESSING" />);
    expect(screen.getByText("PROCESSING").className).toContain("bg-amber-50");

    rerender(<StatusBadge status="FINISHED" />);
    expect(screen.getByText("FINISHED").className).toContain("bg-emerald-50");

    rerender(<StatusBadge status="FAILED" />);
    expect(screen.getByText("FAILED").className).toContain("bg-red-50");
  });
});
