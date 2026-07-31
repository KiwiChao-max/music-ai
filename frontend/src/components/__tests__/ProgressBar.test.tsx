import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProgressBar } from "../ProgressBar";

describe("ProgressBar", () => {
  it("renders progress bar with correct ARIA attributes", () => {
    render(<ProgressBar value={42} />);
    const bar = screen.getByRole("progressbar");
    expect(bar).toBeInTheDocument();
    expect(bar).toHaveAttribute("aria-valuenow", "42");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
  });

  it("displays percentage text", () => {
    render(<ProgressBar value={75} />);
    expect(screen.getByText("75%")).toBeInTheDocument();
  });

  it("clamps values above 100 to 100", () => {
    render(<ProgressBar value={200} />);
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "100");
    expect(screen.getByText("100%")).toBeInTheDocument();
  });

  it("clamps negative values to 0", () => {
    render(<ProgressBar value={-10} />);
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "0");
    expect(screen.getByText("0%")).toBeInTheDocument();
  });

  it("renders 10 cells (filled + empty block characters)", () => {
    const { container } = render(<ProgressBar value={50} />);
    // 5 filled + 5 empty cells for 50%
    const mono = container.querySelector(".font-mono");
    expect(mono?.textContent).toContain("█");
    expect(mono?.textContent).toContain("░");
  });

  it("accepts additional className", () => {
    render(<ProgressBar value={0} className="custom-class" />);
    expect(screen.getByRole("progressbar").className).toContain("custom-class");
  });
});
