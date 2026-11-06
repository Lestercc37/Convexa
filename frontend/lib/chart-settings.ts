"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { getChartSettings, putChartSettings } from "@/lib/api";

// Each trader's chart template (Lester 2026-11-06: "que las plantillas de cada trader se guarden para no activar desde cero todos los días").
// One object per user on the server (GET / PUT /api/v1/auth/me/chart-settings, table user_chart_settings) holding every chart toggle and
// choice (heatmap and its figure, GEX profile, smile, flow, cubes, lines, timeframe, gamma tab, EMAs, cube minimums). It applies to EVERY
// symbol (the chart is rebuilt on each switch and reads it again) and on every device. A copy in this browser makes the first paint instant;
// the server's copy replaces it as soon as it arrives (except a key changed meanwhile), and every change is written back after SAVE_DELAY_MS.
const LOCAL_KEY = "convexa.chartSettings.v1";
const SAVE_DELAY_MS = 800;

type Settings = Record<string, unknown>;

let settings: Settings = readLocal();
let serverLoaded = false;
let loading: Promise<void> | null = null;
let saveTimer: ReturnType<typeof setTimeout> | null = null;
const changedBeforeLoad = new Set<string>();
const listeners = new Set<() => void>();

function readLocal(): Settings {
  try {
    if (typeof window === "undefined") return {};
    const raw = window.localStorage.getItem(LOCAL_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function writeLocal(): void {
  try {
    window.localStorage.setItem(LOCAL_KEY, JSON.stringify(settings));
  } catch {
    // a private window or blocked storage: the server copy still works
  }
}

function emit(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function scheduleSave(): void {
  if (!serverLoaded) return; // written right after the server copy arrives (loadChartSettings)
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    saveTimer = null;
    void putChartSettings(settings).catch(() => undefined); // a failed write is retried by the next change
  }, SAVE_DELAY_MS);
}

/** Reads the trader's template from the server once per page (later calls share the first). */
export function loadChartSettings(): Promise<void> {
  if (loading) return loading;
  loading = getChartSettings()
    .then((response) => {
      const fromServer = response.settings && typeof response.settings === "object" ? response.settings : {};
      const keptLocal: Settings = {};
      for (const key of changedBeforeLoad) keptLocal[key] = settings[key];
      settings = { ...fromServer, ...keptLocal };
      serverLoaded = true;
      writeLocal();
      emit();
      if (changedBeforeLoad.size) scheduleSave();
      changedBeforeLoad.clear();
    })
    .catch(() => {
      loading = null; // not logged in yet, or offline: the browser copy keeps working and the next chart asks again
    });
  return loading;
}

export function getChartSetting(key: string): unknown {
  return settings[key];
}

export function setChartSetting(key: string, value: unknown): void {
  settings = { ...settings, [key]: value };
  if (!serverLoaded) changedBeforeLoad.add(key);
  writeLocal();
  emit();
  scheduleSave();
}

/** Test hook: forget everything (module memory and the browser copy). */
export function resetChartSettingsForTests(): void {
  settings = {};
  serverLoaded = false;
  loading = null;
  changedBeforeLoad.clear();
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = null;
}

function sameKind<T>(value: unknown, fallback: T): value is T {
  if (value === undefined || value === null) return false;
  if (Array.isArray(fallback)) return Array.isArray(value);
  return typeof value === typeof fallback;
}

/**
 * One saved choice of the template: [value, setter], like useState. `fallback` when the template has none (or a value of another kind).
 * `preset` (a shared view link that sets this choice) wins for this chart and is NOT saved: a link must not rewrite the trader's template.
 */
export function useChartSetting<T>(key: string, fallback: T, preset?: T): [T, (next: T | ((previous: T) => T)) => void] {
  const stored = useSyncExternalStore(subscribe, () => settings[key], () => undefined);
  const [presetValue, setPresetValue] = useState<T | undefined>(preset);
  const usingPreset = preset !== undefined;
  useEffect(() => {
    void loadChartSettings();
  }, []);
  const value = usingPreset ? (presetValue as T) : sameKind(stored, fallback) ? stored : fallback;
  const setValue = useCallback(
    (next: T | ((previous: T) => T)) => {
      if (usingPreset) {
        setPresetValue((previous) => (typeof next === "function" ? (next as (p: T) => T)(previous as T) : next));
        return;
      }
      const current = settings[key];
      const previous = sameKind(current, fallback) ? current : fallback;
      setChartSetting(key, typeof next === "function" ? (next as (p: T) => T)(previous) : next);
    },
    // fallback is a literal default at each call site
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, usingPreset],
  );
  return [value, setValue];
}
