"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ApiError, getMe } from "@/lib/api";

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
      .then(() => setStatus("authenticated"))
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        if (reason instanceof ApiError && reason.status === 401) {
          router.replace("/login");
          return;
        }
        // Any other failure (network blip, 500) -- still send to login
        // rather than getting stuck on a blank "checking" screen forever.
        router.replace("/login");
      });
    return () => controller.abort();
  }, [router]);

  if (status === "checking") return null;
  return <>{children}</>;
}
