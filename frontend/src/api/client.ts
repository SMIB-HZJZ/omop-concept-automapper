import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "./schema";

/** Typed API client. Paths come from the backend's OpenAPI spec (`npm run gen:api`). */
export const api = createClient<paths>({ baseUrl: "", credentials: "include" });

export const UNAUTHORIZED_EVENT = "omop:unauthorized";

const authMiddleware: Middleware = {
  onResponse({ request, response }) {
    if (response.status === 401 && !request.url.includes("/api/auth/login")) {
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    }
    return response;
  },
};
api.use(authMiddleware);

export class ApiError extends Error {
  status: number;
  /** The API's ``detail``: a message, validation errors, or an object such as a conflict */
  detail: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

/** A 409 from saving a mapping: someone else mapped the term (e.g. meanwhile). */
export type AlreadyMapped = {
  code: "already_mapped";
  message: string;
  mapped_by: string | null;
  mapped_at: string | null;
  concept_ids: number[];
};

export function alreadyMapped(error: unknown): AlreadyMapped | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = error.detail as Partial<AlreadyMapped> | null;
  return detail && typeof detail === "object" && detail.code === "already_mapped" ? (detail as AlreadyMapped) : null;
}

type ValidationItem = { loc?: (string | number)[]; msg?: string };

export function errorMessage(error: unknown): string {
  if (!error) return "Unknown error";
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  const detail = (error as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string")
    return (detail as { message: string }).message;
  if (Array.isArray(detail)) {
    return (detail as ValidationItem[])
      .map((d) => `${(d.loc ?? []).filter((p) => p !== "body").join(".")}: ${d.msg ?? ""}`.replace(/^: /, ""))
      .join("; ");
  }
  return JSON.stringify(error);
}

/** Returns `data` or throws an ApiError carrying the API's `detail` message. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    const detail = (result.error as { detail?: unknown } | undefined)?.detail;
    throw new ApiError(errorMessage(result.error ?? result.response.statusText), result.response.status, detail);
  }
  return result.data as T;
}
