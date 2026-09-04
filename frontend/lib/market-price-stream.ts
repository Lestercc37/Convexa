// Real-time chart price push -- GET /market/{symbol}/history seeds the
// chart, GET /market/{symbol} (30s poll, see dashboard.tsx) keeps it
// current as a fallback, and this WebSocket updates the in-progress
// candle the instant the Worker persists a new tick (backend/core/
// price_notifications.py forwards its Postgres NOTIFY here). The 30s
// poll is deliberately left running alongside this, not replaced --
// if this connection never opens, or drops and doesn't come back
// (network hiccup, backend restart), the chart keeps working exactly
// as it did before this existed, just back to 30s-stale.

export type MarketPriceTick = {
  symbol: string;
  price: string;
  as_of: string;
};

function marketPriceStreamUrl(symbol: string): string {
  const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${wsProtocol}//${window.location.host}/backend/api/v1/ws/market/${encodeURIComponent(symbol)}`;
}

// No reconnect loop here, deliberately: the 30s poll is the fallback
// this is additive to, not a channel that must never go quiet. A
// symbol/timeframe change or unmount already tears this down via the
// returned cleanup function (dashboard.tsx's effect), same as every
// other per-symbol connection in this codebase (see PriceChart's own
// remount-on-symbol-change convention).
export function connectMarketPriceStream(
  symbol: string,
  onTick: (tick: MarketPriceTick) => void,
): () => void {
  let socket: WebSocket | null = null;
  try {
    socket = new WebSocket(marketPriceStreamUrl(symbol));
  } catch {
    // Some browsers throw synchronously for a malformed URL rather
    // than failing async via onerror -- either way, the 30s poll
    // already covers this symbol, so there's nothing else to do here.
    return () => {};
  }

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
    // onclose for a WebSocket, and there's nothing actionable to do
    // here that the poll isn't already covering.
  };

  return () => {
    socket?.close();
  };
}
