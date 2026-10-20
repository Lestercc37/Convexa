"use client";

import { useEffect, useState } from "react";
import { useLanguage } from "@/lib/i18n/language-context";
import { EASTERN_TIME_ZONE } from "@/lib/market-session";

// The market's clock (New York time, with seconds) fixed in the top bar, next to the log-out button. Empty on the server and on the first client render (no hydration mismatch), then it ticks once a second.

const FORMAT = new Intl.DateTimeFormat("en-GB", {
  timeZone: EASTERN_TIME_ZONE,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function SessionClock() {
  const { t } = useLanguage();
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    const tick = () => setNow(new Date());
    tick();
    const interval = window.setInterval(tick, 1000);
    return () => window.clearInterval(interval);
  }, []);

  return (
    <span className="tv-session-clock" role="timer" aria-label={t.dashboard.clockAriaLabel} title={t.dashboard.clockTitle}>
      {now ? `${FORMAT.format(now)} ET` : "--:--:-- ET"}
    </span>
  );
}
