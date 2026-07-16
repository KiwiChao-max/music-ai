import { Suspense, lazy } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";

import { MainLayout } from "@/layouts/MainLayout";
import { HomePage } from "@/pages/HomePage";

// Route-level code splitting: each page is its own chunk so the initial
// bundle only carries the layout + home page. Heavier pages (AudioDetailPage
// pulls in wavesurfer.js, SampleLibraryPage pulls in the sample player) are
// fetched on demand when the user navigates to them.
const AudioDetailPage = lazy(() =>
  import("@/pages/AudioDetailPage").then((m) => ({ default: m.AudioDetailPage })),
);
const AudioListPage = lazy(() =>
  import("@/pages/AudioListPage").then((m) => ({ default: m.AudioListPage })),
);
const SampleLibraryPage = lazy(() =>
  import("@/pages/SampleLibraryPage").then((m) => ({
    default: m.SampleLibraryPage,
  })),
);
const UploadPage = lazy(() =>
  import("@/pages/UploadPage").then((m) => ({ default: m.UploadPage })),
);
const LoginPage = lazy(() =>
  import("@/pages/LoginPage").then((m) => ({ default: m.LoginPage })),
);
const RegisterPage = lazy(() =>
  import("@/pages/RegisterPage").then((m) => ({ default: m.RegisterPage })),
);

function PageFallback() {
  return (
    <div className="flex items-center justify-center py-24">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-300 border-t-slate-900 dark:border-slate-700 dark:border-t-slate-100" />
    </div>
  );
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <MainLayout />,
    children: [
      { index: true, element: <HomePage /> },
      {
        path: "upload",
        element: (
          <Suspense fallback={<PageFallback />}>
            <UploadPage />
          </Suspense>
        ),
      },
      {
        path: "audio",
        element: (
          <Suspense fallback={<PageFallback />}>
            <AudioListPage />
          </Suspense>
        ),
      },
      {
        path: "audio/:id",
        element: (
          <Suspense fallback={<PageFallback />}>
            <AudioDetailPage />
          </Suspense>
        ),
      },
      {
        path: "instruments",
        element: (
          <Suspense fallback={<PageFallback />}>
            <SampleLibraryPage />
          </Suspense>
        ),
      },
      {
        path: "login",
        element: (
          <Suspense fallback={<PageFallback />}>
            <LoginPage />
          </Suspense>
        ),
      },
      {
        path: "register",
        element: (
          <Suspense fallback={<PageFallback />}>
            <RegisterPage />
          </Suspense>
        ),
      },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);