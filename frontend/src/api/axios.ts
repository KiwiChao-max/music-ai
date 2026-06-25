import axios from "axios";

// In dev, Vite proxies `/api/*` to the FastAPI backend (see vite.config.ts).
// In prod, set VITE_API_BASE_URL to the deployed backend origin.
export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "/api",
  timeout: 30_000,
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    // Surface a single normalized error message for callers.
    const detail = error?.response?.data?.detail ?? error.message;
    return Promise.reject(new Error(detail));
  }
);
