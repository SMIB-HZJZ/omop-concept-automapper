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

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

type ValidationItem = { loc?: (string | number)[]; msg?: string };

export function errorMessage(error: unknown): string {
  if (!error) return "Unknown error";
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  const detail = (error as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
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
    throw new ApiError(errorMessage(result.error ?? result.response.statusText), result.response.status);
  }
  return result.data as T;
}
