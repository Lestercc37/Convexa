"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ApiError, getMe } from "@/lib/api";
import { DESKTOP_HOME, isDesktopApp } from "@/lib/desktop-app";

// Wraps the dashboard route only (app/page.tsx) -- /login itself is a
// separate, ungated route, so there's no path-based branching needed
// here to avoid a redirect loop. Checks the session once on mount via
// /auth/me rather than trusting any client-side "am I logged in" state,
// since the only real source of truth is the httponly session cookie the
// server already validates on every request.
export function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [status, setStatus] = useState<"checking" | "authenticated">("checking");

  useEffect(() => {
    const controller = new AbortController();
    getMe(controller.signal)
      .then(() => {
        // The desktop app never shows the web dashboard: if a window lands on "/" (signup, an old link), it goes to the app's chart.
        if (window.location.pathname === "/" && isDesktopApp()) {
          router.replace(DESKTOP_HOME);
          return;
        }
        setStatus("authenticated");
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        // A /vista/... window (desktop app) comes back to its own view after the login, not to the full dashboard.
        const here = `${window.location.pathname}${window.location.search}`;
        const target = here.startsWith("/vista/") ? `/login?next=${encodeURIComponent(here)}` : "/login";
        if (reason instanceof ApiError && reason.status === 401) {
          router.replace(target);
          return;
        }
        // Any other failure (network blip, 500) -- still send to login
        // rather than getting stuck on a blank "checking" screen forever.
        router.replace(target);
      });
    return () => controller.abort();
  }, [router]);

  if (status === "checking") return null;
  return <>{children}</>;
}
