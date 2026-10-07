import { useCallback, useEffect, useState } from "react";

/** useState backed by localStorage (per browser); falls back to memory when storage is unavailable. */
export function usePersistentState<T>(key: string, initial: T) {
  const storageKey = `omop:${key}`;
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = window.localStorage.getItem(storageKey);
      return raw === null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(value));
    } catch {
      /* storage unavailable: keep the in-memory value */
    }
  }, [storageKey, value]);

  // Keep tabs/pages using the same key in sync
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === storageKey && event.newValue !== null) {
        try {
          setValue(JSON.parse(event.newValue) as T);
        } catch {
          /* ignore malformed values */
        }
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [storageKey]);

  const reset = useCallback(() => setValue(initial), [initial]);
  return [value, setValue, reset] as const;
}
