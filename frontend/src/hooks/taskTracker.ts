import { useSyncExternalStore } from "react";

/** IDs of tasks started from this browser tab. The task watcher polls each one until it finishes,
 * so even a task that ends before the next poll of the active-task list is noticed. */
let tracked: readonly string[] = [];
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

export function trackTask(id: string) {
  if (tracked.includes(id)) return;
  tracked = [...tracked, id];
  emit();
}

export function untrackTask(id: string) {
  if (!tracked.includes(id)) return;
  tracked = tracked.filter((t) => t !== id);
  emit();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useTrackedTaskIds() {
  return useSyncExternalStore(
    subscribe,
    () => tracked,
    () => tracked,
  );
}
