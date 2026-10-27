"use client";

// The page's error boundary: instead of Next's blank "This page couldn't load", it keeps the error in this browser (lib/crash-log.ts) and offers to retry
// without a full reload. The dashboard's own state (symbol, tab, EMAs) lives in localStorage, so a retry or a reload comes back to the same view.

import { useEffect } from "react";
import { recordCrash } from "@/lib/crash-log";

export default function DashboardError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    recordCrash(error);
    console.error("Convexa dashboard crashed", error);
  }, [error]);

  return (
    <main className="dashboard-crash" role="alert">
      <h1>El dashboard tuvo un error</h1>
      <p>Quedó guardado en este navegador para revisarlo. Puedes reintentar sin recargar la página.</p>
      <pre className="dashboard-crash-message">{error?.message || "Error desconocido"}</pre>
      <div className="dashboard-crash-actions">
        <button type="button" onClick={() => reset()}>
          Reintentar
        </button>
        <button type="button" onClick={() => window.location.reload()}>
          Recargar página
        </button>
      </div>
    </main>
  );
}
