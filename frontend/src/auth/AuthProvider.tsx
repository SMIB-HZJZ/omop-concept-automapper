import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, type ReactNode } from "react";
import { toast } from "sonner";

import { api, unwrap, UNAUTHORIZED_EVENT } from "@/api/client";
import { keys } from "@/api/queries";
import type { User } from "@/api/types";
import { AuthContext } from "@/auth/useAuth";

/** Drop all cached server data except the current-user query, then set who is signed in.
 * (queryClient.clear() would detach the mounted `me` observer, so guards would see stale data.) */
function resetSession(qc: QueryClient, user: User | null) {
  qc.removeQueries({ predicate: (query) => query.queryKey[0] !== keys.me[0] });
  qc.setQueryData(keys.me, user);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const me = useQuery({
    queryKey: keys.me,
    queryFn: async () => {
      const result = await api.GET("/api/auth/me");
      if (result.response.status === 401) return null;
      return unwrap(result);
    },
    retry: false,
    staleTime: 5 * 60_000,
  });

  useEffect(() => {
    const onUnauthorized = () => {
      if (!qc.getQueryData(keys.me)) return;
      // The session expired (or was revoked) mid-work. RequireAuth sends the user to the login page,
      // which returns them to the page they were on.
      resetSession(qc, null);
      toast.error("Your session has expired", { description: "Please sign in again.", id: "session-expired" });
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [qc]);

  const login = useCallback(
    async (username: string, password: string) => {
      const result = unwrap(await api.POST("/api/auth/login", { body: { username, password } }));
      resetSession(qc, result.user);
      return result.user;
    },
    [qc],
  );

  /** Always signs out locally, even when the server cannot be reached (the cookie then expires on its own). */
  const logout = useCallback(async () => {
    try {
      await api.POST("/api/auth/logout");
    } catch {
      toast.warning("Signed out locally", { description: "The server could not be reached to end the session." });
    } finally {
      resetSession(qc, null);
    }
  }, [qc]);

  const user = me.data ?? null;
  return (
    <AuthContext.Provider value={{ user, isLoading: me.isLoading, isAdmin: user?.role === "admin", login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
