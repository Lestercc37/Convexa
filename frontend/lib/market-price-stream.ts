// Real-time chart price push -- GET /market/{symbol}/history seeds the
// chart, GET /market/{symbol} (30s poll, see dashboard.tsx) keeps it
// current as a fallback, and this WebSocket updates the in-progress
// candle the instant the Worker persists a new tick (backend/core/
// price_notifications.py forwards its Postgres NOTIFY here). The 30s
// poll is deliberately left running alongside this, not replaced --
// if this connection never opens, or drops and doesn't come back, the
// chart keeps working exactly as it did before this existed, just
// back to 30s-stale.

export type MarketPriceTick = {
  symbol: string;
  price: string;
  as_of: string;
};

export type MarketPriceStreamStatus = "connected" | "fallback";

// Not routed through the /backend/* rewrite (next.config.ts) like every
// other API call -- confirmed live, 2026-09-29, straight from
// cloudflared's own log: `error="...HTTP/1.x transport connection
// broken: malformed HTTP status code \"Server\""` recurring
// intermittently (not only at a backend restart boundary) specifically
// for `dest=.../backend/api/v1/ws/market/{symbol} ... type=ws`, a real
// protocol-level incompatibility between cloudflared's own WebSocket
// upgrade handling and Next.js's rewrite-based proxying -- every other
// (plain HTTP) route through the same rewrite is unaffected. Matched
// this session's real symptom exactly: the chart worked in real time
// for ~30-40s, stalled ~10s while market-price-stream.ts's own
// reconnect backoff ran, then resumed -- repeating for as long as the
// tab stayed open. cloudflared-config.yml (V:/Convexa/services on the
// server, not tracked in this repo) now routes this exact path straight
// to the backend's own port (8000), bypassing Next.js's rewrite/proxy
// entirely for this one connection -- everything else still goes
// through port 3000 unchanged.
function marketPriceStreamUrl(symbol: string): string {
  const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${wsProtocol}//${window.location.host}/api/v1/ws/market/${encodeURIComponent(symbol)}`;
}

const INITIAL_RECONNECT_DELAY_MS = 1_000;
// Caps at the same cadence as the 30s poll this stream is additive to
// (dashboard.tsx's own POLLING_INTERVAL_MS) -- no reason to hammer the
// backend faster than the fallback path this is racing against anyway.
const MAX_RECONNECT_DELAY_MS = 30_000;

// Reconnects automatically with exponential backoff instead of going
// silently quiet forever on the first drop -- confirmed live, 2026-09:
// repeated backend restarts that week left long-open tabs on a dead
// socket with no visible sign anything had changed, silently falling
// back to 30s-stale prices. onStatusChange lets the caller show a
// subtle indicator while running on the fallback poll instead of this
// stream, without this module knowing anything about the UI.
export function connectMarketPriceStream(
  symbol: string,
  onTick: (tick: MarketPriceTick) => void,
  onStatusChange?: (status: MarketPriceStreamStatus) => void,
): () => void {
  let socket: WebSocket | null = null;
  let reconnectTimer: number | undefined;
  let reconnectDelay = INITIAL_RECONNECT_DELAY_MS;
  let stopped = false;

  const scheduleReconnect = () => {
    if (stopped) return;
    onStatusChange?.("fallback");
    reconnectTimer = window.setTimeout(() => {
      reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY_MS);
      connect();
    }, reconnectDelay);
  };

  const connect = () => {
    try {
      socket = new WebSocket(marketPriceStreamUrl(symbol));
    } catch {
      // Some browsers throw synchronously for a malformed URL rather
      // than failing async via onerror -- retried the same as any
      // other drop, since the URL doesn't change between attempts.
      scheduleReconnect();
      return;
    }

    socket.onopen = () => {
      reconnectDelay = INITIAL_RECONNECT_DELAY_MS;
      onStatusChange?.("connected");
    };

    socket.onmessage = (event: MessageEvent<string>) => {
      try {
        const tick = JSON.parse(event.data) as MarketPriceTick;
        if (tick.symbol && tick.price && tick.as_of) onTick(tick);
      } catch {
        // Malformed frame -- ignored, same "don't let one bad message
        // take down the whole stream" stance as every other consumer of
        // provider-originated data in this codebase.
      }
    };
    socket.onerror = () => {
      // No explicit handling beyond this: onerror is always followed by
      // onclose for a WebSocket, which is where reconnection is scheduled.
    };
    socket.onclose = scheduleReconnect;
  };

  connect();

  return () => {
    stopped = true;
    if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
    if (socket) {
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      socket.close();
    }
  };
}
