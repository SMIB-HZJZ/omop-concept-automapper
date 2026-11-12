import { usePersistentState } from "@/hooks/usePersistentState";

/** Default confidence threshold (1–10) of the Streamlit app. */
export const DEFAULT_THRESHOLD = 8;

/** Confidence threshold shared by "Auto map settings" and the per-concept "AI suggest". */
export function useAutomapThreshold() {
  return usePersistentState("automap-threshold", DEFAULT_THRESHOLD);
}
