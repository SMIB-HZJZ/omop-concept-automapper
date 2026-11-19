import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";

import { useAuth } from "@/auth/useAuth";
import { EmptyState } from "@/components/common";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth();
  const location = useLocation();
  if (isLoading) return <div className="text-muted-foreground p-10 text-center text-sm">Loading…</div>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return children;
}

export function RequireAdmin({ children }: { children: ReactNode }) {
  const { isAdmin } = useAuth();
  if (!isAdmin)
    return (
      <EmptyState title="Admins only">
        This page is for administrators. Mapping, review and search are in the menu on the left.
      </EmptyState>
    );
  return children;
}
