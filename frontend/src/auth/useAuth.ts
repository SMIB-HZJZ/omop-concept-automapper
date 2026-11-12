import { createContext, useContext } from "react";

import type { User } from "@/api/types";

export type AuthState = {
  user: User | null;
  isLoading: boolean;
  isAdmin: boolean;
  login: (username: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
};

export const AuthContext = createContext<AuthState | null>(null);

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
