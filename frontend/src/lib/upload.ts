import { ApiError, UNAUTHORIZED_EVENT, errorMessage } from "@/api/client";

export type UploadHandle<T> = { promise: Promise<T>; abort: () => void };

/** POSTs a file as the raw request body with upload progress (fetch cannot report it).
 * Uses the session cookie like the API client; rejects with an ApiError carrying `detail`. */
export function uploadFile<T>(url: string, file: File, onProgress: (loaded: number, total: number) => void) {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<T>((resolve, reject) => {
    xhr.open("POST", url);
    xhr.withCredentials = true;
    xhr.setRequestHeader("Content-Type", file.type || "application/zip");
    xhr.upload.onprogress = (e) => onProgress(e.loaded, e.lengthComputable ? e.total : file.size);
    xhr.onload = () => {
      let body: unknown;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        body = xhr.responseText;
      }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body as T);
      if (xhr.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
      const message =
        xhr.status === 413 && typeof body !== "object"
          ? "The file is larger than the server accepts"
          : errorMessage(body ?? xhr.statusText);
      reject(new ApiError(message, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError("The upload failed (network error)", 0));
    xhr.onabort = () => reject(new ApiError("Upload cancelled", 0));
    xhr.send(file);
  });
  return { promise, abort: () => xhr.abort() } satisfies UploadHandle<T>;
}
