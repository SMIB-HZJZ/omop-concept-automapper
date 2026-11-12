/** ATC level-5 code: letter, 2 digits, 2 letters, 2 digits (e.g. A10BA02). Same rule as the backend. */
export const ATC7_RE = /^[A-Z]\d{2}[A-Z]{2}\d{2}$/;

export function normalizeAtc7(value: string): string {
  return value.trim().toUpperCase();
}

export function isValidAtc7(value: string): boolean {
  return ATC7_RE.test(normalizeAtc7(value));
}
