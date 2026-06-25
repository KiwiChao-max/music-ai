import { createBrowserRouter, Navigate } from "react-router-dom";

import { MainLayout } from "@/layouts/MainLayout";
import { AudioDetailPage } from "@/pages/AudioDetailPage";
import { AudioListPage } from "@/pages/AudioListPage";
import { HomePage } from "@/pages/HomePage";
import { UploadPage } from "@/pages/UploadPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <MainLayout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "upload", element: <UploadPage /> },
      { path: "audio", element: <AudioListPage /> },
      { path: "audio/:id", element: <AudioDetailPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);
