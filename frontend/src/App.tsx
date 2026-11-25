import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";
import { lazy, Suspense, type ReactNode } from "react";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router";

import { ApiError } from "@/api/client";
import { AuthProvider } from "@/auth/AuthProvider";
import { RequireAdmin, RequireAuth } from "@/auth/guards";
import { EmptyState } from "@/components/common";
import { Layout } from "@/components/Layout";
import { PageSkeleton } from "@/components/motion";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { LoginPage } from "@/pages/Login";

// Pages are code-split; each loads on first visit
const ImportPage = lazy(() => import("@/pages/Import").then((m) => ({ default: m.ImportPage })));
const SearchPage = lazy(() => import("@/pages/Search").then((m) => ({ default: m.SearchPage })));
const GuidePage = lazy(() => import("@/pages/Guide").then((m) => ({ default: m.GuidePage })));
const ExportPage = lazy(() => import("@/pages/Export").then((m) => ({ default: m.ExportPage })));
const MapPage = lazy(() => import("@/pages/Map").then((m) => ({ default: m.MapPage })));
const ReviewPage = lazy(() => import("@/pages/Review").then((m) => ({ default: m.ReviewPage })));
const CommitPage = lazy(() => import("@/pages/Commit").then((m) => ({ default: m.CommitPage })));
const SettingsPage = lazy(() => import("@/pages/Settings").then((m) => ({ default: m.SettingsPage })));
const UsersPage = lazy(() => import("@/pages/Users").then((m) => ({ default: m.UsersPage })));

const page = (element: ReactNode) => <Suspense fallback={<PageSkeleton />}>{element}</Suspense>;

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
    },
  },
});

const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: (
      <RequireAuth>
        <Layout />
      </RequireAuth>
    ),
    children: [
      // Mapping is the main page for everyone
      { index: true, element: <Navigate to="/map" replace /> },
      { path: "import", element: <RequireAdmin>{page(<ImportPage />)}</RequireAdmin> },
      { path: "search", element: page(<SearchPage />) },
      { path: "export", element: page(<ExportPage />) },
      { path: "guide", element: page(<GuidePage />) },
      { path: "map", element: page(<MapPage />) },
      { path: "review", element: page(<ReviewPage />) },
      { path: "commit", element: page(<CommitPage />) },
      { path: "settings", element: <RequireAdmin>{page(<SettingsPage />)}</RequireAdmin> },
      {
        path: "users",
        element: <RequireAdmin>{page(<UsersPage />)}</RequireAdmin>,
      },
      { path: "*", element: <EmptyState title="Page not found" /> },
    ],
  },
]);

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      {/* Animations follow the operating system's "reduce motion" setting */}
      <MotionConfig reducedMotion="user">
        <TooltipProvider>
          <AuthProvider>
            <RouterProvider router={router} />
          </AuthProvider>
          <Toaster richColors position="bottom-right" />
        </TooltipProvider>
      </MotionConfig>
    </QueryClientProvider>
  );
}
