import { useEffect, useMemo, useState } from "react";

import { getAccessToken } from "@/api/axios";

/**
 * Append the auth token as a query parameter to artifact URLs so that
 * ``<a href>``, ``<audio>``, and other elements that cannot set HTTP headers
 * still pass the ownership check on the backend.
 *
 * Polls the in-memory token and re-renders once a token becomes available
 * (e.g. after a page-refresh cookie-based token refresh).
 */
export function useArtifactUrl(url: string): string {
  const [token, setToken] = useState<string | null>(() => getAccessToken());

  useEffect(() => {
    if (token) return;
    const id = setInterval(() => {
      const t = getAccessToken();
      if (t) {
        setToken(t);
        clearInterval(id);
      }
    }, 200);
    return () => clearInterval(id);
  }, [token]);

  return useMemo(() => {
    if (!token) return url;
    const sep = url.includes("?") ? "&" : "?";
    return `${url}${sep}token=${encodeURIComponent(token)}`;
  }, [url, token]);
}