import axios, { type AxiosError, type AxiosResponse } from "axios";

// In dev, Vite proxies `/api/*` to the FastAPI backend (see vite.config.ts).
// In prod, set VITE_API_BASE_URL to the deployed backend origin.
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30_000,
  withCredentials: true, // send cookies (refresh token, CSRF token)
});

// ---------------------------------------------------------------------------
// Token helpers
// ---------------------------------------------------------------------------

/** Access token kept in memory only --- never persisted to localStorage.
 *  A page refresh means the SPA calls /api/auth/refresh to get a new
 *  access token via the HttpOnly cookie. */
let _cachedAccessToken: string | null = null;

/** CSRF token cached in memory.  Read from the login/register response
 *  and sent back as the X-CSRF-Token header on state-changing requests. */
let _cachedCsrfToken: string | null = null;

/** Call this from AuthProvider to prime the cache after login/refresh. */
export function setCachedTokens(accessToken: string | null, csrfToken: string | null): void {
  _cachedAccessToken = accessToken;
  _cachedCsrfToken = csrfToken;
}

/** Synchronous read --- used by the request interceptor and WebSocket hooks. */
export function getAccessToken(): string | null {
  return _cachedAccessToken;
}

// ---------------------------------------------------------------------------
// Request interceptor --- Bearer token + CSRF header
// ---------------------------------------------------------------------------
api.interceptors.request.use((config) => {
  // Attach Bearer token if we have one in memory.
  if (_cachedAccessToken && config.headers) {
    config.headers.Authorization = `Bearer ${_cachedAccessToken}`;
  }

  // Attach CSRF token for state-changing methods (skip auth endpoints
  // because login/register don't have a CSRF cookie yet).
  const method = (config.method ?? "").toUpperCase();
  if (
    _cachedCsrfToken &&
    config.headers &&
    ["POST", "PUT", "PATCH", "DELETE"].includes(method) &&
    !config.url?.startsWith("/auth/")
  ) {
    config.headers["X-CSRF-Token"] = _cachedCsrfToken;
  }

  return config;
});

// ---------------------------------------------------------------------------
// Response interceptor --- 401 -> try cookie-based refresh
// ---------------------------------------------------------------------------
let _isRefreshing = false;
let _refreshPromise: Promise<boolean> | null = null;

async function _doRefresh(): Promise<boolean> {
  try {
    // /api/auth/refresh reads the refresh token from the HttpOnly cookie
    // (no token in the body).  We use a raw axios instance to avoid the
    // interceptor loop.
    const resp = await axios.post<TokenRefreshResponse>(
      `${API_BASE_URL}/auth/refresh`,
      {},
      { withCredentials: true },
    );
    _cachedAccessToken = resp.data.access_token;
    _cachedCsrfToken = resp.data.csrf_token;
    return true;
  } catch {
    return false;
  }
}

function _queueRefresh(): Promise<boolean> {
  if (_refreshPromise) return _refreshPromise;
  _isRefreshing = true;
  _refreshPromise = _doRefresh().finally(() => {
    _isRefreshing = false;
    _refreshPromise = null;
  });
  return _refreshPromise;
}

interface TokenRefreshResponse {
  access_token: string;
  csrf_token: string;
}

/** Callback registered by AuthProvider so the interceptor can clear the session. */
let _onForceLogout: (() => void) | null = null;
export function setForceLogoutHandler(fn: (() => void) | null) {
  _onForceLogout = fn;
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const status = error.response?.status;
    const isAuthEndpoint = error.config?.url?.startsWith("/auth/");

    if (status === 401 && !isAuthEndpoint && error.config) {
      // If another request is already refreshing, wait for it.
      if (_isRefreshing) {
        const ok = await _queueRefresh();
        if (ok && error.config.headers) {
          error.config.headers.Authorization = `Bearer ${_cachedAccessToken}`;
          if (_cachedCsrfToken) {
            error.config.headers["X-CSRF-Token"] = _cachedCsrfToken;
          }
          return api(error.config);
        }
        // Refresh failed --- clear session and reject.
        _cachedAccessToken = null;
        _cachedCsrfToken = null;
        _onForceLogout?.();
        return Promise.reject(error);
      }

      // Try a single refresh.
      const ok = await _queueRefresh();
      if (ok && error.config.headers) {
        error.config.headers.Authorization = `Bearer ${_cachedAccessToken}`;
        if (_cachedCsrfToken) {
          error.config.headers["X-CSRF-Token"] = _cachedCsrfToken;
        }
        return api(error.config);
      }

      // Refresh failed --- clear session and redirect to login.
      _cachedAccessToken = null;
      _cachedCsrfToken = null;
      _onForceLogout?.();
    }

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

// ---------------------------------------------------------------------------
// ApiError
// ---------------------------------------------------------------------------

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
