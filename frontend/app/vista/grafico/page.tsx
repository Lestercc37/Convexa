"use client";

import { Suspense, useCallback } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { AuthGate } from "@/components/auth-gate";
import { Dashboard } from "@/components/dashboard";
import { chartRouteQuery, parseChartRoute, type ChartRouteState } from "@/lib/view-route";

// The chart alone, with its symbol / timeframe / range / tab / layer in the address (docs/diseno-app-escritorio-2026-10-26.txt section 5). The
// desktop app's main window opens it plain and each layer window opens it with `capa`; it works in a browser tab too.
function ChartView() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const route = parseChartRoute(new URLSearchParams(params.toString()));
  const onRouteChange = useCallback(
    (state: ChartRouteState) => {
      const query = chartRouteQuery(state);
      if (query !== window.location.search.replace(/^\?/, "")) router.replace(`${pathname}?${query}`, { scroll: false });
    },
    [router, pathname],
  );
  return <Dashboard mode="chart" route={route} onRouteChange={onRouteChange} />;
}

export default function ChartViewPage() {
  return (
    <AuthGate>
      <Suspense fallback={null}>
        <ChartView />
      </Suspense>
    </AuthGate>
  );
}
