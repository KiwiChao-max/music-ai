/**
 * Centralised auth state management.
 *
 * One provider wraps the app and exposes the current user, tokens, and
 * login/register/logout actions.
 *
 * Security model
 * --------------
 * * **access token** --- short-lived (15 min), kept in memory only.
 *   A page refresh triggers a silent cookie-based refresh via
 *   ``/api/auth/refresh`` because the HttpOnly refresh cookie survives
 *   the reload.
 * * **refresh token** --- stored in an HttpOnly / Secure / SameSite=Strict
 *   cookie.  XSS cannot read it, and CSRF cannot exploit it (the
 *   ``X-CSRF-Token`` header is required for state-changing requests).
 * * **CSRF token** --- a separate non-HttpOnly cookie that the SPA reads
 *   from the login/register response and sends back as ``X-CSRF-Token``
 *   on every POST/PUT/PATCH/DELETE.
 * * **No localStorage** --- nothing auth-related is persisted there.
 *   localStorage is readable by any script on the same origin, so
 *   storing tokens there amplifies the impact of XSS.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";

import { authApi } from "@/api/auth";
import { setCachedTokens, setForceLogoutHandler } from "@/api/axios";
import type { UserPublic } from "@/api/auth";

interface AuthState {
  user: UserPublic | null;
  /** `true` while we are restoring the session from the HttpOnly cookie. */
  isLoading: boolean;
  isAuthenticated: boolean;
}

interface AuthActions {
  login: (identifier: string, password: string) => Promise<void>;
  register: (
    email: string,
    username: string,
    password: string,
    fullName?: string,
  ) => Promise<void>;
  logout: () => Promise<void>;
}

type AuthContextValue = AuthState & AuthActions;

const AuthContext = createContext<AuthContextValue | null>(null);

interface AuthProviderProps {
  children: ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [user, setUser] = useState<UserPublic | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const logoutInProgress = useRef(false);

  // On mount: try a cookie-based refresh to restore the session.
  // The HttpOnly refresh cookie is sent automatically by the browser.
  useEffect(() => {
    let cancelled = false;
    authApi
      .refresh()
      .then((resp) => {
        if (cancelled) return;
        setCachedTokens(resp.access_token, resp.csrf_token);
        setUser(resp.user);
      })
      .catch(() => {
        if (cancelled) return;
        // No valid refresh cookie --- the user is unauthenticated.
        setCachedTokens(null, null);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(
    async (identifier: string, password: string) => {
      const resp = await authApi.login(identifier, password);
      setCachedTokens(resp.access_token, resp.csrf_token);
      setUser(resp.user);
    },
    [],
  );

  const register = useCallback(
    async (
      email: string,
      username: string,
      password: string,
      fullName?: string,
    ) => {
      const resp = await authApi.register(email, username, password, fullName);
      setCachedTokens(resp.access_token, resp.csrf_token);
      setUser(resp.user);
    },
    [],
  );

  const logout = useCallback(async () => {
    if (logoutInProgress.current) return;
    logoutInProgress.current = true;
    try {
      await authApi.logout().catch(() => {
        // Best-effort --- server may already have revoked the token.
      });
    } finally {
      setCachedTokens(null, null);
      setUser(null);
      logoutInProgress.current = false;
    }
  }, []);

  // Register the force-logout handler so the axios 401 interceptor can
  // clear the session without a circular dependency.
  useEffect(() => {
    setForceLogoutHandler(() => {
      setCachedTokens(null, null);
      setUser(null);
    });
    return () => setForceLogoutHandler(null);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      isLoading,
      isAuthenticated: user !== null,
      login,
      register,
      logout,
    }),
    [user, isLoading, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return ctx;
}