import { useMemo } from "react";

import { getAccessToken } from "@/api/axios";

/**
 * Append the auth token as a query parameter to artifact URLs so that
 * ``<a href>``, ``<audio>``, and other elements that cannot set HTTP headers
 * still pass the ownership check on the backend.
 *
 * Reads the token from the shared ``getAccessToken()`` helper rather than
 * duplicating localStorage access --- the same source as the axios interceptor
 * and the WebSocket hook.
 */
export function useArtifactUrl(url: string): string {
  return useMemo(() => {
    const token = getAccessToken();
    if (!token) return url;
    const sep = url.includes("?") ? "&" : "?";
    return `${url}${sep}token=${encodeURIComponent(token)}`;
  }, [url]);
}