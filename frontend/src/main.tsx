import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";

import { AuthProvider } from "@/contexts/AuthContext";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { PlayerProvider } from "@/contexts/PlayerContext";
import "@/i18n";
import { router } from "@/router";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      // Static reference data (sample libraries, drum types, GM
      // instruments, soundfonts) doesn't change between mounts; without a
      // staleTime every navigation refetched it. Mutations invalidate the
      // affected keys, so 5 minutes of staleness can't go stale on the
      // user. Task queries poll explicitly via refetchInterval and are
      // unaffected.
      staleTime: 5 * 60 * 1000,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <PlayerProvider>
            <RouterProvider router={router} />
          </PlayerProvider>
        </AuthProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
);
