// A small, local-only flight recorder for the chart's lifecycle. 2026-10-16: the price chart emptied at 16:00 ET (every series, not only
// the candles) and refilled ~5 s later; the server side showed no gap, restart or tunnel event, and nothing in the code remounts the chart
// except a symbol/timeframe change. This records what the BROWSER saw (page loads and their navigation type, chart mounts/unmounts,
// tab visibility, network up/down, price-stream status) with a timestamp, so the next occurrence can be read instead of guessed at.
//
// Nothing leaves the browser. Read it from the console:  JSON.parse(localStorage["convexa.chartLifecycle"])  or  convexaChartLog()
// Every storage access is wrapped: a private window or blocked storage just records nothing.

export const CHART_LIFECYCLE_STORAGE_KEY = "convexa.chartLifecycle";
export const CHART_LIFECYCLE_MAX_ENTRIES = 80;

export type ChartLifecycleDetail = Record<string, string | number | boolean | null>;
export type ChartLifecycleEntry = { at: string; event: string; detail: ChartLifecycleDetail };

type StorageLike = Pick<Storage, "getItem" | "setItem">;

function browserStorage(): StorageLike | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function readChartEvents(storage: StorageLike | null = browserStorage()): ChartLifecycleEntry[] {
  if (!storage) return [];
  try {
    const parsed: unknown = JSON.parse(storage.getItem(CHART_LIFECYCLE_STORAGE_KEY) ?? "[]");
    return Array.isArray(parsed) ? (parsed as ChartLifecycleEntry[]) : [];
  } catch {
    return [];
  }
}

export function recordChartEvent(
  event: string,
  detail: ChartLifecycleDetail = {},
  storage: StorageLike | null = browserStorage(),
  now: Date = new Date(),
): void {
  if (!storage) return;
  try {
    const entries = readChartEvents(storage);
    entries.push({ at: now.toISOString(), event, detail });
    storage.setItem(CHART_LIFECYCLE_STORAGE_KEY, JSON.stringify(entries.slice(-CHART_LIFECYCLE_MAX_ENTRIES)));
  } catch {
    // quota or blocked storage: a diagnostic must never break the page
  }
}

// How this page load started: "navigate" (typed/linked), "reload", "back_forward", or "prerender"; null when the browser does not say.
export function navigationType(): string | null {
  try {
    const entry = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
    return entry?.type ?? null;
  } catch {
    return null;
  }
}

/**
 * Long tasks of the page (the main thread busy for 200 ms or more): a chart whose canvas was cleared by a resize stays blank until the main thread is free to repaint it,
 * so a long task right before the chart comes back is the usual suspect for "the chart disappears for ~10 s after loading" (2026-10-18). Recorded as "long-task" with the
 * duration and when it started (ms since the page began). Returns the cleanup; a browser without the Long Tasks API records nothing.
 */
export function observeLongTasks(record: typeof recordChartEvent = recordChartEvent): () => void {
  if (typeof PerformanceObserver === "undefined") return () => undefined;
  try {
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (entry.duration >= 200) record("long-task", { ms: Math.round(entry.duration), startedAtMs: Math.round(entry.startTime) });
      }
    });
    observer.observe({ type: "longtask", buffered: true });
    return () => observer.disconnect();
  } catch {
    return () => undefined;
  }
}

// Page-level signals that can explain a chart that empties and refills: the page itself (re)loading, the tab hidden/shown (browsers pause
// and resume timers and sockets), and the network dropping. Returns the cleanup that removes the listeners.
export function startChartLifecycleRecorder(): () => void {
  if (typeof window === "undefined") return () => undefined;
  recordChartEvent("page-load", { navigation: navigationType(), visibility: document.visibilityState });
  const onVisibility = () => recordChartEvent("visibility", { state: document.visibilityState });
  const onPageShow = (event: PageTransitionEvent) => recordChartEvent("pageshow", { fromBackForwardCache: event.persisted });
  const onOnline = () => recordChartEvent("network", { online: true });
  const onOffline = () => recordChartEvent("network", { online: false });
  // Uncaught errors too (2026-10-27, the dashboard dropping after adding EMAs): only the first 160 characters of the message, so a crash can be matched to what preceded it.
  const onError = (event: ErrorEvent) => recordChartEvent("window-error", { message: String(event.message ?? "").slice(0, 160) });
  window.addEventListener("error", onError);
  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("pageshow", onPageShow);
  window.addEventListener("online", onOnline);
  window.addEventListener("offline", onOffline);
  (window as unknown as { convexaChartLog?: () => ChartLifecycleEntry[] }).convexaChartLog = () => readChartEvents();
  const stopLongTasks = observeLongTasks();
  return () => {
    stopLongTasks();
    document.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("pageshow", onPageShow);
    window.removeEventListener("online", onOnline);
    window.removeEventListener("offline", onOffline);
    window.removeEventListener("error", onError);
  };
}
