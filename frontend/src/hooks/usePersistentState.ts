import { useCallback, useMemo, useState, useSyncExternalStore } from "react";

/** Per-browser UI preferences in localStorage, shared live between every component (and tab)
 * using the same key. Falls back to memory when storage is unavailable. */

const memory = new Map<string, string>();
const listeners = new Map<string, Set<() => void>>();

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return memory.get(key) ?? null;
  }
}

function write(key: string, raw: string) {
  try {
    window.localStorage.setItem(key, raw);
  } catch {
    memory.set(key, raw);
  }
  notify(key);
}

function notify(key: string) {
  for (const listener of listeners.get(key) ?? []) listener();
}

function subscribe(key: string, listener: () => void) {
  let set = listeners.get(key);
  if (!set) listeners.set(key, (set = new Set()));
  set.add(listener);
  // Changes made in other tabs
  const onStorage = (event: StorageEvent) => {
    if (event.key === key) listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    set.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

function parse<T>(raw: string | null, fallback: T): T {
  if (raw === null) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function usePersistentState<T>(key: string, initial: T) {
  const storageKey = `omop:${key}`;
  // The first initial value wins, so callers may pass a fresh literal on every render
  const [fallback] = useState(initial);
  const raw = useSyncExternalStore(
    useCallback((listener: () => void) => subscribe(storageKey, listener), [storageKey]),
    () => read(storageKey),
    () => null,
  );
  const value = useMemo(() => parse(raw, fallback), [raw, fallback]);

  const setValue = useCallback(
    (next: T | ((previous: T) => T)) => {
      const previous = parse(read(storageKey), fallback);
      const resolved = typeof next === "function" ? (next as (previous: T) => T)(previous) : next;
      write(storageKey, JSON.stringify(resolved));
    },
    [storageKey, fallback],
  );

  const reset = useCallback(() => write(storageKey, JSON.stringify(fallback)), [storageKey, fallback]);
  return [value, setValue, reset] as const;
}
