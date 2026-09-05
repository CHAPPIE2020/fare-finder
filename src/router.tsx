import { createBrowserRouter, Navigate } from "react-router-dom";

import { RootLayout } from "@/layouts/RootLayout";
import { ProtectedRoute } from "@/routes/ProtectedRoute";
import { RouteErrorBoundary } from "@/routes/RouteErrorBoundary";
import { Landing } from "@/pages/Landing";
import { AuthPage } from "@/pages/AuthPage";
import { AppShell } from "@/pages/AppShell";
import { NotFoundPage } from "@/pages/NotFoundPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <RootLayout />,
    errorElement: <RouteErrorBoundary />,
    children: [
      { index: true, element: <Landing /> },
      { path: "sign-in", element: <AuthPage mode="signin" /> },
      { path: "sign-up", element: <AuthPage mode="signup" /> },
      // Legacy link kept working: the previous single `/auth` toggle page.
      { path: "auth", element: <Navigate to="/sign-in" replace /> },
      {
        element: <ProtectedRoute />,
        children: [{ path: "app", element: <AppShell /> }],
      },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
