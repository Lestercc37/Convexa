"use client";

import { useEffect } from "react";

// Keeps every open Convexa page fresh without anyone pressing F5 (Lester 2026-11-06: Yaser's and Alberto's charts still lose data after their
// PCs sleep). Two cases reload the page once the trader is not using it:
// - WAKE: the page's own timer skipped more than WAKE_GAP_MS (the computer slept, or the browser froze the tab). The day's chart, heatmap,
//   flow and cubes are all re-read from scratch, as a symbol switch does; the chart choices survive (lib/chart-settings.ts). PR #394 only
//   re-read prices and VWAP, and only in pages loaded after it was deployed.
// - NEW VERSION: the build stamp published by the server (/build-stamp) differs from the one this page was built with: a page left open
//   for days never ran the fixes deployed meanwhile.
// Never more than one automatic reload per MIN_RELOAD_GAP_MS (a loop guard kept in sessionStorage).
export const WAKE_GAP_MS = 2 * 60_000;
export const IDLE_BEFORE_UPDATE_MS = 2 * 60_000;
export const VERSION_CHECK_MS = 5 * 60_000;
export const MIN_RELOAD_GAP_MS = 60_000;
const TICK_MS = 10_000;
const LAST_RELOAD_KEY = "convexa.autoReloadAt";
export const BUILD_STAMP = process.env.NEXT_PUBLIC_BUILD_STAMP ?? "";

export type FreshnessState = {
  now: number;
  visible: boolean;
  wokeUp: boolean;
  newVersion: boolean;
  lastInteractionAt: number;
  lastAutoReloadAt: number;
};

/** Whether to reload now: after a wake as soon as the page is visible, for a new version once the trader has been idle; never twice a minute. */
export function shouldReload(state: FreshnessState): boolean {
  if (!state.visible || state.now - state.lastAutoReloadAt < MIN_RELOAD_GAP_MS) return false;
  if (state.wokeUp) return true;
  return state.newVersion && state.now - state.lastInteractionAt >= IDLE_BEFORE_UPDATE_MS;
}

function readLastReload(): number {
  try {
    return Number(window.sessionStorage.getItem(LAST_RELOAD_KEY)) || 0;
  } catch {
    return 0;
  }
}

function reload(): void {
  try {
    window.sessionStorage.setItem(LAST_RELOAD_KEY, String(Date.now()));
  } catch {
    // no session storage: the loop guard is lost, the reload still happens
  }
  window.location.reload();
}

export function useAppFreshness(): void {
  useEffect(() => {
    let lastTickAt = Date.now();
    let lastInteractionAt = Date.now();
    let lastVersionCheckAt = 0;
    let wokeUp = false;
    let newVersion = false;
    const check = () => {
      const state: FreshnessState = {
        now: Date.now(),
        visible: document.visibilityState === "visible",
        wokeUp,
        newVersion,
        lastInteractionAt,
        lastAutoReloadAt: readLastReload(),
      };
      if (shouldReload(state)) reload();
    };
    const onTick = () => {
      const now = Date.now();
      if (now - lastTickAt > WAKE_GAP_MS) wokeUp = true;
      lastTickAt = now;
      if (BUILD_STAMP && now - lastVersionCheckAt >= VERSION_CHECK_MS) {
        lastVersionCheckAt = now;
        fetch(`/build-stamp?t=${now}`, { cache: "no-store" })
          .then((response) => (response.ok ? response.json() : null))
          .then((body: { stamp?: string } | null) => {
            if (body?.stamp && body.stamp !== BUILD_STAMP) newVersion = true;
          })
          .catch(() => undefined);
      }
      check();
    };
    const onInteraction = () => {
      lastInteractionAt = Date.now();
    };
    const onVisible = () => {
      if (Date.now() - lastTickAt > WAKE_GAP_MS) wokeUp = true;
      check();
    };
    const timer = window.setInterval(onTick, TICK_MS);
    document.addEventListener("visibilitychange", onVisible);
    for (const name of ["pointerdown", "keydown", "wheel"] as const) window.addEventListener(name, onInteraction, { passive: true });
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      for (const name of ["pointerdown", "keydown", "wheel"] as const) window.removeEventListener(name, onInteraction);
    };
  }, []);
}
