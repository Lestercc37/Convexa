"use client";

import { Suspense, useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { AuthGate } from "@/components/auth-gate";
import { getUnderlyings } from "@/lib/api";
import type { Underlying } from "@/lib/types";

/**
 * A desktop-app window for one panel (Dealers, Alertas), opened from "Ventanas" (Lester 2026-10-30: the main window stays a plain chart, each panel lives
 * in its own window). The symbol is in the address (?simbolo=SPX) and can be changed here; the window keeps the v2 look of /vista.
 */
function PanelWindowInner({ title, children }: { title: string; children: (symbol: string) => ReactNode }) {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const symbol = (params.get("simbolo") ?? "SPX").toUpperCase();
  const [underlyings, setUnderlyings] = useState<Underlying[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    getUnderlyings(controller.signal)
      .then(({ underlyings: items }) => setUnderlyings(items))
      .catch(() => {
        // The symbol picker is a convenience: the panel still works with the address's symbol.
      });
    return () => controller.abort();
  }, []);

  return (
    <main className="tv-shell v2-panel-window">
      <header className="v2-win-head">
        <b>{title}</b>
        <select
          className="v2-sel"
          aria-label="Símbolo"
          value={symbol}
          onChange={(event) => router.replace(`${pathname}?simbolo=${encodeURIComponent(event.target.value)}`, { scroll: false })}
        >
          {(underlyings.length ? underlyings.map((item) => item.symbol) : [symbol]).map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      </header>
      <div className="v2-panel-body">{children(symbol)}</div>
    </main>
  );
}

export function PanelWindow({ title, children }: { title: string; children: (symbol: string) => ReactNode }) {
  return (
    <AuthGate>
      <Suspense fallback={null}>
        <PanelWindowInner title={title}>{children}</PanelWindowInner>
      </Suspense>
    </AuthGate>
  );
}
