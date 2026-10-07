import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, type ReactNode } from "react";

import { api, unwrap, UNAUTHORIZED_EVENT } from "@/api/client";
import { keys } from "@/api/queries";
import type { User } from "@/api/types";

type AuthState = {
  user: User | null;
  isLoading: boolean;
  isAdmin: boolean;
  login: (username: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

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
      if (qc.getQueryData(keys.me)) resetSession(qc, null);
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

  const logout = useCallback(async () => {
    await api.POST("/api/auth/logout");
    resetSession(qc, null);
  }, [qc]);

  const user = me.data ?? null;
  return (
    <AuthContext.Provider
      value={{ user, isLoading: me.isLoading, isAdmin: user?.role === "admin", login, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
