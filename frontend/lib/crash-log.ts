// When the dashboard crashes ("el dashboard se va y hay que recargar", 2026-10-27, after adding EMAs: not reproducible from another tab), the error that took it down is
// kept in this browser so it can be read afterwards instead of guessed at: message, stack, Next's digest and the last chart lifecycle events (lib/chart-lifecycle.ts).
// Nothing leaves the browser. Read it from the console:  JSON.parse(localStorage["convexa.crashes.v1"])  or  convexaCrashes()

import { readChartEvents } from "./chart-lifecycle";

export const CRASH_STORAGE_KEY = "convexa.crashes.v1";
export const CRASH_MAX_ENTRIES = 10;

export type CrashEntry = {
  at: string;
  message: string;
  stack: string | null;
  digest: string | null;
  url: string | null;
  lastEvents: { at: string; event: string }[];
};

type StorageLike = Pick<Storage, "getItem" | "setItem">;

function browserStorage(): StorageLike | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function readCrashes(storage: StorageLike | null = browserStorage()): CrashEntry[] {
  if (!storage) return [];
  try {
    const parsed: unknown = JSON.parse(storage.getItem(CRASH_STORAGE_KEY) ?? "[]");
    return Array.isArray(parsed) ? (parsed as CrashEntry[]) : [];
  } catch {
    return [];
  }
}

export function recordCrash(error: Error & { digest?: string }, storage: StorageLike | null = browserStorage(), now: Date = new Date()): void {
  if (!storage) return;
  try {
    const entries = readCrashes(storage);
    entries.push({
      at: now.toISOString(),
      message: String(error?.message ?? error),
      stack: typeof error?.stack === "string" ? error.stack.slice(0, 4000) : null,
      digest: error?.digest ?? null,
      url: typeof window === "undefined" ? null : window.location.href,
      lastEvents: readChartEvents(storage).slice(-15).map((entry) => ({ at: entry.at, event: entry.event })),
    });
    storage.setItem(CRASH_STORAGE_KEY, JSON.stringify(entries.slice(-CRASH_MAX_ENTRIES)));
    (window as unknown as { convexaCrashes?: () => CrashEntry[] }).convexaCrashes = () => readCrashes();
  } catch {
    // a diagnostic must never break the page further
  }
}
