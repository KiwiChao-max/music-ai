import axios, { type AxiosError, type AxiosResponse } from "axios";

// In dev, Vite proxies `/api/*` to the FastAPI backend (see vite.config.ts).
// In prod, set VITE_API_BASE_URL to the deployed backend origin.
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30_000,
});

/**
 * Normalized error thrown by every call through this client.
 *
 * - `message` — human-readable detail from the API (`response.data.detail`)
 *   or a network/timeout message when no response was received.
 * - `status` — HTTP status if we got a response; `null` for network errors.
 * - `data` — the full response body, useful for callers that need to tell
 *   404 (not found) from 409 (not ready).
 * - `cause` — the original `AxiosError`, kept on the standard `Error.cause`
 *   property so `console.error(err)` shows the full chain.
 */
export class ApiError extends Error {
  status: number | null;
  data: unknown;

  constructor(
    message: string,
    status: number | null,
    data: unknown,
    options?: { cause?: unknown },
  ) {
    super(message, options);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

function buildErrorMessage(response?: AxiosResponse): string {
  const data = response?.data as { detail?: unknown } | undefined;
  const detail = data?.detail;
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  if (detail !== undefined && detail !== null) {
    try {
      return JSON.stringify(detail);
    } catch {
      return String(detail);
    }
  }
  if (response) {
    return `Request failed with status ${response.status}`;
  }
  return "Network error";
}

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    return Promise.reject(
      new ApiError(
        buildErrorMessage(error.response ?? undefined),
        error.response?.status ?? null,
        error.response?.data ?? null,
        { cause: error },
      ),
    );
  },
);
