import { api } from "./axios";

export interface UserPublic {
  id: number;
  email: string;
  username: string;
  full_name: string | null;
  role: string;
  is_active: boolean;
  max_tasks: number;
  max_upload_bytes: number;
  created_at: string;
  last_login_at: string | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: UserPublic;
  /** CSRF token --- the SPA caches this and sends it as X-CSRF-Token. */
  csrf_token: string;
}

export const authApi = {
  login: (identifier: string, password: string) =>
    api.post<TokenResponse>("/auth/login", { identifier, password }).then((r) => r.data),

  register: (email: string, username: string, password: string, fullName?: string) =>
    api
      .post<TokenResponse>("/auth/register", {
        email,
        username,
        password,
        full_name: fullName,
      })
      .then((r) => r.data),

  /** Refresh the access token.  The refresh token is sent automatically
   *  via the HttpOnly cookie --- no explicit token in the body. */
  refresh: () => api.post<TokenResponse>("/auth/refresh").then((r) => r.data),

  me: () => api.get<UserPublic>("/auth/me").then((r) => r.data),

  logout: () => api.post<{ message: string }>("/auth/logout"),

  /** Fetch a fresh CSRF token (called on app mount). */
  csrf: () => api.get<{ csrf_token: string }>("/auth/csrf").then((r) => r.data),
};
