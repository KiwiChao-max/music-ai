import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";

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
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <PlayerProvider>
          <RouterProvider router={router} />
        </PlayerProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>
);
