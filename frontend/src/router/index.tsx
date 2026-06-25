import { createBrowserRouter, Navigate } from "react-router-dom";

import { MainLayout } from "@/layouts/MainLayout";
import { HomePage } from "@/pages/HomePage";
import { AudioListPage } from "@/pages/AudioListPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <MainLayout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "audio", element: <AudioListPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);
