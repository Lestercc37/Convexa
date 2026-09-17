import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { connectMarketPriceStream } from "./market-price-stream";

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  triggerOpen() {
    this.onopen?.();
  }

  triggerMessage(data: string) {
    this.onmessage?.({ data } as MessageEvent<string>);
  }

  triggerClose() {
    this.onclose?.();
  }
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("connectMarketPriceStream", () => {
  it("forwards well-formed ticks and silently ignores malformed frames", () => {
    const onTick = vi.fn();
    connectMarketPriceStream("SPY", onTick);
    const socket = MockWebSocket.instances[0];

    socket.triggerMessage(
      JSON.stringify({ symbol: "SPY", price: "550", as_of: "2026-01-01T00:00:00Z" }),
    );
    expect(onTick).toHaveBeenCalledWith({
      symbol: "SPY",
      price: "550",
      as_of: "2026-01-01T00:00:00Z",
    });

    expect(() => socket.triggerMessage("not json")).not.toThrow();
    expect(onTick).toHaveBeenCalledTimes(1);
  });

  it("reports connected once the socket opens", () => {
    const onStatusChange = vi.fn();
    connectMarketPriceStream("SPY", vi.fn(), onStatusChange);

    MockWebSocket.instances[0].triggerOpen();

    expect(onStatusChange).toHaveBeenCalledWith("connected");
  });

  it("reconnects with exponential backoff after the socket closes, reporting fallback in between (regression)", () => {
    // Confirmed live, 2026-09-17: repeated backend restarts that week left
    // long-open tabs on a dead socket with no visible sign anything had
    // changed and no automatic recovery -- this is the fix, verified
    // against a fake WebSocket + fake timers rather than a real backend
    // restart.
    vi.useFakeTimers();
    const onStatusChange = vi.fn();
    connectMarketPriceStream("SPY", vi.fn(), onStatusChange);
    expect(MockWebSocket.instances).toHaveLength(1);

    MockWebSocket.instances[0].triggerClose();
    expect(onStatusChange).toHaveBeenLastCalledWith("fallback");
    expect(MockWebSocket.instances).toHaveLength(1);

    vi.advanceTimersByTime(999);
    expect(MockWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(MockWebSocket.instances).toHaveLength(2);

    // Second drop backs off further (2s, doubled from the 1s initial delay)
    // instead of retrying at the same fixed interval forever.
    MockWebSocket.instances[1].triggerClose();
    vi.advanceTimersByTime(1_999);
    expect(MockWebSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(MockWebSocket.instances).toHaveLength(3);
  });

  it("caps the reconnect backoff instead of growing it forever", () => {
    vi.useFakeTimers();
    connectMarketPriceStream("SPY", vi.fn());

    // Every attempt fails immediately -- after enough drops the delay
    // should have hit its 30s ceiling and stayed there.
    for (let attempt = 0; attempt < 8; attempt += 1) {
      MockWebSocket.instances.at(-1)?.triggerClose();
      vi.advanceTimersByTime(30_000);
    }
    const countAfterEightDrops = MockWebSocket.instances.length;

    MockWebSocket.instances.at(-1)?.triggerClose();
    vi.advanceTimersByTime(29_999);
    expect(MockWebSocket.instances).toHaveLength(countAfterEightDrops);
    vi.advanceTimersByTime(1);
    expect(MockWebSocket.instances).toHaveLength(countAfterEightDrops + 1);
  });

  it("resets the backoff delay back to its initial value after a successful reconnect", () => {
    vi.useFakeTimers();
    connectMarketPriceStream("SPY", vi.fn());

    MockWebSocket.instances[0].triggerClose();
    vi.advanceTimersByTime(1_000);
    expect(MockWebSocket.instances).toHaveLength(2);

    MockWebSocket.instances[1].triggerOpen();
    MockWebSocket.instances[1].triggerClose();

    // Backed off to 1s again, not 2s -- the successful open in between
    // reset the delay instead of continuing to double it.
    vi.advanceTimersByTime(999);
    expect(MockWebSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(MockWebSocket.instances).toHaveLength(3);
  });

  it("stops reconnecting once the caller tears down the connection", () => {
    vi.useFakeTimers();
    const disconnect = connectMarketPriceStream("SPY", vi.fn());

    MockWebSocket.instances[0].triggerClose();
    disconnect();
    vi.advanceTimersByTime(60_000);

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0].closed).toBe(true);
  });
});
