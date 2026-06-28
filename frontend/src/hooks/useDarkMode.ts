/**
 * Dark mode toggle.
 *
 * Persists the user's preference in `localStorage` and applies the
 * `dark` class to the `<html>` element. On the very first page load
 * the hook reads `localStorage`; if there's no saved value it falls
 * back to `prefers-color-scheme` so the app matches the OS theme by
 * default.
 *
 * Putting the class on `<html>` (not `<body>` or a wrapper div)
 * matches what Tailwind's `dark:` variant expects and avoids the
 * flash-of-wrong-theme on first paint: we apply the class in an
 * inline `<script>` in `index.html` before React mounts, then keep
 * it in sync from this hook.
 */
import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "music-ai.theme";

function readInitialTheme(): Theme {
  if (typeof window === "undefined") return "light";
  // The inline boot script in index.html has already applied the
  // class, so we can read it back from <html> and stay consistent
  // with whatever the user saw on first paint.
  const fromHtml = document.documentElement.classList.contains("dark");
  if (fromHtml) return "dark";
  return "light";
}

function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (theme === "dark") {
    root.classList.add("dark");
  } else {
    root.classList.remove("dark");
  }
  // The form controls and scrollbars follow the theme.
  root.style.colorScheme = theme;
}

export function useDarkMode(): {
  theme: Theme;
  toggle: () => void;
  setTheme: (theme: Theme) => void;
} {
  const [theme, setThemeState] = useState<Theme>(readInitialTheme);

  // Keep `<html>` in sync if some other tab flips the toggle.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onStorage = (e: StorageEvent) => {
      if (e.key !== STORAGE_KEY) return;
      const next: Theme = e.newValue === "dark" ? "dark" : "light";
      setThemeState(next);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    applyTheme(next);
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // localStorage may be disabled (private mode); the theme
        // still works for the current session.
      }
    }
  }, []);

  const toggle = useCallback(() => {
    setTheme(theme === "dark" ? "light" : "dark");
  }, [theme, setTheme]);

  return { theme, toggle, setTheme };
}
