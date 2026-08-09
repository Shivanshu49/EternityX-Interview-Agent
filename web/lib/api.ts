import type { InterviewResponse } from "./types";

/**
 * Backend base URL. Baked in at build time:
 *   - local dev: defaults to the FastAPI dev server on :8000
 *   - Vercel:    set NEXT_PUBLIC_API_URL in the project's environment variables
 */
export const API_BASE = (
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");

export class ApiError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.status = status;
  }
}

/** FastAPI error bodies: {detail: string} or {detail: [{msg, ...}]} on 422. */
function detailToMessage(data: unknown, res: Response): string {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : null))
        .filter(Boolean);
      if (msgs.length) return `Invalid request: ${msgs.join("; ")}`;
    }
  }
  return `The server responded with ${res.status} ${res.statusText}.`;
}

interface PostOptions {
  explain?: boolean;
  /** Abort the request after this long; the final feedback call needs more. */
  timeoutMs?: number;
}

export async function postInterview(
  body: Record<string, unknown>,
  { explain = false, timeoutMs = 120_000 }: PostOptions = {},
): Promise<InterviewResponse> {
  const url = `${API_BASE}/api/interview${explain ? "?explain=1" : ""}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(
        "The server is taking unusually long. It may still be working - wait a moment, then retry.",
      );
    }
    // Raw fetch rejections read as "Failed to fetch", which tells the user nothing.
    throw new ApiError(
      `Could not reach the interview server at ${API_BASE}. ` +
        "Check that the backend is running and that its CORS settings allow this origin.",
    );
  } finally {
    clearTimeout(timer);
  }

  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON error body; fall through to the status-based message */
  }
  if (!res.ok) throw new ApiError(detailToMessage(data, res), res.status);
  return data as InterviewResponse;
}
