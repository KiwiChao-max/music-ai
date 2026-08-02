import { useMemo } from "react";

/**
 * Prepare an artifact URL for use in ``<a href>``, ``<audio>``, and other
 * elements that cannot set HTTP headers.
 *
 * The backend already appends a short-lived, file-scoped download token
 * (``?token=...``) to artifact URLs at response time (see ``_artifact_url``
 * in the backend). We must NOT append the user's access token here:
 *   - Access tokens in URLs leak via browser history, server logs, and
 *     Referer headers.
 *   - The download endpoint verifies the token against a dedicated
 *     download-token secret; access tokens are NOT valid download tokens.
 *   - Appending a second ``&token=<access_token>`` creates a duplicate
 *     query parameter that can shadow the legitimate download token.
 *
 * If the URL expires (5-minute TTL), consumers should refetch the task
 * data from the API to obtain fresh signed URLs.
 */
export function useArtifactUrl(url: string): string {
  return useMemo(() => url, [url]);
}
