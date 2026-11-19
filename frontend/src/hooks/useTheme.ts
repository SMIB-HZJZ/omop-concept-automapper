import { useEffect } from "react";

import { usePersistentState } from "@/hooks/usePersistentState";

export type Theme = "light" | "dark" | "system";

/** Light, dark or the system's choice; applied as the "dark" class on <html>. */
export function useTheme() {
  const [theme, setTheme] = usePersistentState<Theme>("theme", "system");
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () =>
      document.documentElement.classList.toggle("dark", theme === "dark" || (theme === "system" && media.matches));
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);
  return { theme, setTheme };
}
