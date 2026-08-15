import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { AuthProvider, useAuth } from "@/contexts/AuthContext";

// ---------------------------------------------------------------------------
// Mock react-i18next
// ---------------------------------------------------------------------------
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (k: string) => k, i18n: { language: "en" } }),
}));

// ---------------------------------------------------------------------------
// Mock auth API — must use vi.hoisted to avoid hoisting issues with vi.mock
// ---------------------------------------------------------------------------
const { mockAuthApi, mockSetCachedTokens, mockSetForceLogoutHandler } = vi.hoisted(() => ({
  mockAuthApi: {
    login: vi.fn(),
    register: vi.fn(),
    refresh: vi.fn(),
    logout: vi.fn(),
    me: vi.fn(),
    csrf: vi.fn(),
  },
  mockSetCachedTokens: vi.fn(),
  mockSetForceLogoutHandler: vi.fn(),
}));

vi.mock("@/api/auth", () => ({
  authApi: mockAuthApi,
}));

vi.mock("@/api/axios", () => ({
  setCachedTokens: mockSetCachedTokens,
  setForceLogoutHandler: mockSetForceLogoutHandler,
  getAccessToken: () => null,
  api: {
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  },
}));

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------
function makeUser(overrides = {}) {
  return {
    id: 1,
    email: "test@example.com",
    username: "testuser",
    full_name: "Test User",
    role: "user",
    is_active: true,
    max_tasks: 10,
    max_upload_bytes: 200 * 1024 * 1024,
    created_at: "2026-01-01T00:00:00Z",
    last_login_at: null,
    ...overrides,
  };
}

function getWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={qc}>
        <AuthProvider>{children}</AuthProvider>
      </QueryClientProvider>
    );
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("AuthContext", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default: refresh fails → unauthenticated
    mockAuthApi.refresh.mockRejectedValue(new Error("no cookie"));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("initial session restoration", () => {
    it("starts with isLoading=true, then settles to unauthenticated", async () => {
      const { result } = renderHook(() => useAuth(), { wrapper: getWrapper() });
      expect(result.current.isLoading).toBe(true);
      await waitFor(() => {
        expect(result.current.isLoading).toBe(false);
      });
      expect(result.current.isAuthenticated).toBe(false);
      expect(result.current.user).toBeNull();
    });

    it("restores session when refresh succeeds", async () => {
      const user = makeUser();
      mockAuthApi.refresh.mockResolvedValue({
        access_token: "at",
        csrf_token: "csrf",
        user,
      });
      const { result } = renderHook(() => useAuth(), { wrapper: getWrapper() });
      await waitFor(() => {
        expect(result.current.isLoading).toBe(false);
      });
      expect(result.current.isAuthenticated).toBe(true);
      expect(result.current.user).toEqual(user);
      expect(mockSetCachedTokens).toHaveBeenCalledWith("at", "csrf");
    });
  });

  describe("login", () => {
    it("sets user and tokens on successful login", async () => {
      const user = makeUser();
      mockAuthApi.login.mockResolvedValue({
        access_token: "at",
        csrf_token: "csrf",
        user,
      });
      const { result } = renderHook(() => useAuth(), { wrapper: getWrapper() });
      await waitFor(() => expect(result.current.isLoading).toBe(false));

      await act(() => result.current.login("test@example.com", "pass"));
      expect(result.current.isAuthenticated).toBe(true);
      expect(result.current.user).toEqual(user);
      expect(mockSetCachedTokens).toHaveBeenCalledWith("at", "csrf");
    });

    it("throws on login failure and keeps user as null", async () => {
      mockAuthApi.login.mockRejectedValue(new Error("Invalid credentials"));
      const { result } = renderHook(() => useAuth(), { wrapper: getWrapper() });
      await waitFor(() => expect(result.current.isLoading).toBe(false));

      await expect(act(() => result.current.login("bad@example.com", "wrong"))).rejects.toThrow(
        "Invalid credentials",
      );
      expect(result.current.isAuthenticated).toBe(false);
      expect(result.current.user).toBeNull();
    });
  });

  describe("logout", () => {
    it("clears user and tokens on logout", async () => {
      const user = makeUser();
      mockAuthApi.refresh.mockResolvedValue({
        access_token: "at",
        csrf_token: "csrf",
        user,
      });
      mockAuthApi.logout.mockResolvedValue({ message: "ok" });
      const { result } = renderHook(() => useAuth(), { wrapper: getWrapper() });
      await waitFor(() => expect(result.current.isLoading).toBe(false));
      expect(result.current.isAuthenticated).toBe(true);

      await act(() => result.current.logout());
      expect(result.current.isAuthenticated).toBe(false);
      expect(result.current.user).toBeNull();
      expect(mockSetCachedTokens).toHaveBeenCalledWith(null, null);
    });

    it("clears session even when server logout fails", async () => {
      const user = makeUser();
      mockAuthApi.refresh.mockResolvedValue({
        access_token: "at",
        csrf_token: "csrf",
        user,
      });
      mockAuthApi.logout.mockRejectedValue(new Error("network error"));
      const { result } = renderHook(() => useAuth(), { wrapper: getWrapper() });
      await waitFor(() => expect(result.current.isLoading).toBe(false));

      await act(() => result.current.logout());
      expect(result.current.isAuthenticated).toBe(false);
      expect(result.current.user).toBeNull();
    });
  });

  describe("force logout handler", () => {
    it("registers a force-logout handler that clears the session", async () => {
      const user = makeUser();
      mockAuthApi.refresh.mockResolvedValue({
        access_token: "at",
        csrf_token: "csrf",
        user,
      });
      const { result } = renderHook(() => useAuth(), { wrapper: getWrapper() });
      await waitFor(() => expect(result.current.isLoading).toBe(false));

      // The force-logout handler was registered via setForceLogoutHandler.
      const handler = mockSetForceLogoutHandler.mock.calls[0]?.[0];
      expect(handler).toBeDefined();
      act(() => handler());
      expect(result.current.isAuthenticated).toBe(false);
    });
  });
});

describe("useAuth outside provider", () => {
  it("throws when used outside AuthProvider", () => {
    // Suppress the expected error from React's error boundary
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => renderHook(() => useAuth())).toThrow("useAuth must be used inside <AuthProvider>");
    consoleError.mockRestore();
  });
});
