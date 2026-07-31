import { renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useDarkMode } from "../useDarkMode";

describe("useDarkMode", () => {
  const STORAGE_KEY = "music-ai.theme";

  beforeEach(() => {
    document.documentElement.classList.remove("dark");
    document.documentElement.style.colorScheme = "";
    window.localStorage.clear();
  });

  afterEach(() => {
    document.documentElement.classList.remove("dark");
    document.documentElement.style.colorScheme = "";
    window.localStorage.clear();
  });

  it("defaults to light when html has no dark class and no storage value", () => {
    const { result } = renderHook(() => useDarkMode());
    expect(result.current.theme).toBe("light");
  });

  it("reads initial dark mode from <html> class (set by boot script)", () => {
    document.documentElement.classList.add("dark");
    const { result } = renderHook(() => useDarkMode());
    expect(result.current.theme).toBe("dark");
  });

  it("toggle switches theme and persists to localStorage", () => {
    const { result } = renderHook(() => useDarkMode());
    expect(result.current.theme).toBe("light");

    act(() => {
      result.current.toggle();
    });

    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.style.colorScheme).toBe("dark");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("dark");

    act(() => {
      result.current.toggle();
    });

    expect(result.current.theme).toBe("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.documentElement.style.colorScheme).toBe("light");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("light");
  });

  it("setTheme applies explicit theme", () => {
    const { result } = renderHook(() => useDarkMode());

    act(() => {
      result.current.setTheme("dark");
    });
    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    act(() => {
      result.current.setTheme("light");
    });
    expect(result.current.theme).toBe("light");
  });

  it("clamps invalid storage values (cross-tab sync)", () => {
    const { result } = renderHook(() => useDarkMode());

    // Simulate cross-tab storage event with a valid value
    act(() => {
      window.dispatchEvent(
        new StorageEvent("storage", {
          key: STORAGE_KEY,
          newValue: "dark",
        }),
      );
    });
    expect(result.current.theme).toBe("dark");

    // Non-"dark" values should switch to light
    act(() => {
      window.dispatchEvent(
        new StorageEvent("storage", {
          key: STORAGE_KEY,
          newValue: "invalid",
        }),
      );
    });
    expect(result.current.theme).toBe("light");
  });

  it("ignores storage events for unrelated keys", () => {
    const { result } = renderHook(() => useDarkMode());

    act(() => {
      window.dispatchEvent(
        new StorageEvent("storage", {
          key: "some-other-key",
          newValue: "dark",
        }),
      );
    });
    expect(result.current.theme).toBe("light");
  });
});
