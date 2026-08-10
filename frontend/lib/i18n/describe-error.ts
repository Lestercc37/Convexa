import { ApiError } from "@/lib/api";
import type { Translations } from "./translations";

// Every request path funnels its catch block through this instead of
// `reason.message` — `getJson` (lib/api.ts) always throws a real `Error`,
// so `reason.message` always won and the raw, untranslated
// `API request failed (${status})` string was what users actually saw,
// never the friendly fallback text.
export function describeError(reason: unknown, t: Translations): string {
  if (reason instanceof ApiError && reason.status === 404) return t.errors.notFound;
  return t.errors.requestFailed;
}
