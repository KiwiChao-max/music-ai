import { useMemo } from "react";

const STORAGE_KEY = "music-ai.token";

function readToken(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

/**
 * Append the auth token as a query parameter to artifact URLs so that
 * `<a href>`, `<audio>`, and other elements that cannot set HTTP headers
 * still pass the ownership check on the backend.
 */
export function useArtifactUrl(url: string): string {
  return useMemo(() => {
    const token = readToken();
    if (!token) return url;
    const sep = url.includes("?") ? "&" : "?";
    return `${url}${sep}token=${encodeURIComponent(token)}`;
  }, [url]);
}