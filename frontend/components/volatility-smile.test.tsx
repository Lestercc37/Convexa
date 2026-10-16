import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import type { OptionChainResponse, OptionContract } from "@/lib/types";
import { VolatilitySmile } from "./volatility-smile";

const apiMocks = vi.hoisted(() => ({
  getOptionChain: vi.fn(),
  getOptionChainExpirations: vi.fn(),
}));

// The fixtures' option expirations are fixed dates in August 2026; "today" is pinned to match,
// since the Smile no longer asks for expirations that already expired.
const marketSessionMocks = vi.hoisted(() => ({
  currentEasternDate: vi.fn(() => "2026-08-03"),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getOptionChain: apiMocks.getOptionChain,
    getOptionChainExpirations: apiMocks.getOptionChainExpirations,
  };
});

vi.mock("@/lib/market-session", async () => {
  const actual = await vi.importActual<typeof import("@/lib/market-session")>("@/lib/market-session");
  return { ...actual, currentEasternDate: marketSessionMocks.currentEasternDate };
});

function contract(
  occSymbol: string,
  strike: number,
  expiration: string,
  type: "call" | "put",
  iv: number,
): OptionContract {
  return {
    occ_symbol: occSymbol,
    strike,
    expiration,
    type,
    bid: 1,
    ask: 1.1,
    iv,
    delta: 0.5,
    gamma: 0.02,
    theta: -0.01,
    vega: 0.1,
    charm: -0.001,
    vanna: 0.01,
    open_interest: 1000,
    volume: 500,
  };
}

const contracts = [
  contract("SPY260807C00545000", 545, "2026-08-07", "call", 0.22),
  contract("SPY260807P00550000", 550, "2026-08-07", "put", 0.19),
  contract("SPY260807C00555000", 555, "2026-08-07", "call", 0.21),
  contract("SPY260814C00550000", 550, "2026-08-14", "call", 0.2),
  contract("SPY260814P00555000", 555, "2026-08-14", "put", 0.23),
];

function chain(filteredContracts: OptionContract[]): OptionChainResponse {
  return {
    schema_version: 1,
    symbol: "SPY",
    as_of: "2026-08-03T14:30:00Z",
    spot_price: 551,
    contracts: filteredContracts,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  marketSessionMocks.currentEasternDate.mockReturnValue("2026-08-03");
  apiMocks.getOptionChainExpirations.mockResolvedValue({
    schema_version: 1,
    symbol: "SPY",
    expirations: [...new Set(contracts.map((item) => item.expiration))],
  });
  apiMocks.getOptionChain.mockImplementation((_symbol: string, expiration?: string) =>
    Promise.resolve(
      chain(expiration ? contracts.filter((item) => item.expiration === expiration) : contracts),
    ),
  );
});

describe("VolatilitySmile", () => {
  it("renders raw call/put IV points, derives expirations and marks ATM", async () => {
    const user = userEvent.setup();
    renderWithLanguage(<VolatilitySmile symbol="SPY" marketPrice={551} />);

    const selector = await screen.findByLabelText("Vencimiento");
    await waitFor(() => expect(selector).toHaveValue("2026-08-07"));
    expect(withinOptions(selector)).toEqual(["2026-08-07", "2026-08-14"]);
    expect(await screen.findByLabelText("Strike ATM 550")).toBeInTheDocument();
    expect(screen.getByLabelText("call strike 545, IV 22.00%")).toBeInTheDocument();
    expect(screen.getByLabelText("put strike 550, IV 19.00%")).toBeInTheDocument();
    expect(screen.getByText(/no es un nivel de gravitación/)).toBeInTheDocument();

    await user.selectOptions(selector, "2026-08-14");
    await waitFor(() =>
      expect(apiMocks.getOptionChain).toHaveBeenCalledWith(
        "SPY",
        "2026-08-14",
        expect.any(AbortSignal),
      ),
    );
    expect(await screen.findByLabelText("call strike 550, IV 20.00%")).toBeInTheDocument();
    expect(screen.queryByLabelText("call strike 545, IV 22.00%")).not.toBeInTheDocument();
  });

  it("re-polls the option chain every 30s instead of freezing at the value fetched on mount (regression)", async () => {
    // Confirmed live, 2026-09-17: IV per contract moves within a session
    // the same as everything else derived from the option chain, but this
    // panel only ever fetched once per symbol/expiration change -- same
    // 30s cadence as dashboard.tsx's own live refresh (polling.ts's
    // POLLING_INTERVAL_MS), not a new one invented for this panel.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderWithLanguage(<VolatilitySmile symbol="SPY" marketPrice={551} />);

    await vi.waitFor(() => expect(apiMocks.getOptionChain).toHaveBeenCalledTimes(1));

    await vi.advanceTimersByTimeAsync(30_000);
    await vi.waitFor(() => expect(apiMocks.getOptionChain).toHaveBeenCalledTimes(2));

    vi.useRealTimers();
  });

  it("populates the expiration dropdown from the lightweight expirations endpoint, not the full chain (regression)", async () => {
    // getOptionChain (the full, per-contract chain) must never be called
    // without an expiration -- confirmed live, 2026-09-22: doing so just
    // to read off `.expiration` grew to ~8,000 contracts / 2.3MB for SPX
    // after the Gamma Flip wide-search fix (PR #159), heavy enough to
    // help starve /chain/{symbol}'s shared threadpool into real 500s.
    renderWithLanguage(<VolatilitySmile symbol="SPY" marketPrice={551} />);

    await screen.findByLabelText("Vencimiento");
    expect(apiMocks.getOptionChainExpirations).toHaveBeenCalledWith(
      "SPY",
      expect.any(AbortSignal),
    );
    expect(apiMocks.getOptionChain).not.toHaveBeenCalledWith(
      "SPY",
      undefined,
      expect.any(AbortSignal),
    );
  });

  it("gives each IV point a hover tooltip with the same text as its aria-label (regression)", async () => {
    renderWithLanguage(<VolatilitySmile symbol="SPY" marketPrice={551} />);

    const point = await screen.findByLabelText("call strike 545, IV 22.00%");
    expect(point.querySelector("title")).toHaveTextContent("call strike 545, IV 22.00%");
  });

  it("never offers or asks for an expiration that already expired", async () => {
    apiMocks.getOptionChainExpirations.mockResolvedValue({
      schema_version: 1,
      symbol: "SPY",
      expirations: ["2026-08-01", "2026-08-03", "2026-08-07", "2026-08-14"],
    });
    renderWithLanguage(<VolatilitySmile symbol="SPY" marketPrice={551} />);

    const selector = await screen.findByLabelText("Vencimiento");
    await waitFor(() => expect(selector).toHaveValue("2026-08-03"));
    expect(withinOptions(selector)).toEqual(["2026-08-03", "2026-08-07", "2026-08-14"]);
    const requested = apiMocks.getOptionChain.mock.calls.map((call) => call[1]);
    expect(requested).not.toContain("2026-08-01");
  });

  it("moves to the nearest current expiration by itself when the panel stays open past the selected date", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    apiMocks.getOptionChainExpirations
      .mockResolvedValueOnce({ schema_version: 1, symbol: "SPY", expirations: ["2026-08-03", "2026-08-07"] })
      .mockResolvedValue({ schema_version: 1, symbol: "SPY", expirations: ["2026-08-03", "2026-08-07", "2026-08-14"] });
    renderWithLanguage(<VolatilitySmile symbol="SPY" marketPrice={551} />);
    const selector = await screen.findByLabelText("Vencimiento");
    await vi.waitFor(() => expect(selector).toHaveValue("2026-08-03"));

    marketSessionMocks.currentEasternDate.mockReturnValue("2026-08-04");   // next day, same open tab
    apiMocks.getOptionChain.mockClear();
    await vi.advanceTimersByTimeAsync(30_000);

    await vi.waitFor(() => expect(selector).toHaveValue("2026-08-07"));
    expect(withinOptions(selector)).toEqual(["2026-08-07", "2026-08-14"]);
    expect(apiMocks.getOptionChain).not.toHaveBeenCalledWith("SPY", "2026-08-03", expect.anything());
    vi.useRealTimers();
  });

  it("says there is no current expiration (and asks for none) when every stored date has expired", async () => {
    apiMocks.getOptionChainExpirations.mockResolvedValue({
      schema_version: 1,
      symbol: "SPY",
      expirations: ["2026-07-30", "2026-07-31"],
    });
    renderWithLanguage(<VolatilitySmile symbol="SPY" marketPrice={551} />);

    expect(await screen.findByText(/No hay vencimientos vigentes por ahora/)).toBeInTheDocument();
    expect(apiMocks.getOptionChain).not.toHaveBeenCalled();
  });

  it("keeps looking for a current expiration when there is none, and starts asking for it as soon as one appears", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    apiMocks.getOptionChainExpirations
      .mockResolvedValueOnce({ schema_version: 1, symbol: "SPY", expirations: ["2026-07-31"] })
      .mockResolvedValue({ schema_version: 1, symbol: "SPY", expirations: ["2026-07-31", "2026-08-03"] });
    renderWithLanguage(<VolatilitySmile symbol="SPY" marketPrice={551} />);
    expect(await screen.findByText(/No hay vencimientos vigentes por ahora/)).toBeInTheDocument();
    expect(apiMocks.getOptionChain).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(30_000);

    await vi.waitFor(() =>
      expect(apiMocks.getOptionChain).toHaveBeenCalledWith("SPY", "2026-08-03", expect.anything()),
    );
    vi.useRealTimers();
  });

  it("draws only the strikes within 1.5% of spot, so far wings do not stretch the plot", async () => {
    const wide = [
      contract("SPY260807C00545000", 545, "2026-08-07", "call", 0.22),
      contract("SPY260807P00550000", 550, "2026-08-07", "put", 0.19),
      contract("SPY260807C00600000", 600, "2026-08-07", "call", 0.5),
      contract("SPY260807P00500000", 500, "2026-08-07", "put", 0.6),
    ];
    apiMocks.getOptionChain.mockResolvedValue(chain(wide));
    renderWithLanguage(<VolatilitySmile symbol="SPY" marketPrice={551} />);

    expect(await screen.findByLabelText("call strike 545, IV 22.00%")).toBeInTheDocument();
    expect(screen.getByLabelText("put strike 550, IV 19.00%")).toBeInTheDocument();
    expect(screen.queryByLabelText("call strike 600, IV 50.00%")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("put strike 500, IV 60.00%")).not.toBeInTheDocument();
    // Out-of-window contracts are not "missing IV": no note for them.
    expect(screen.queryByText(/sin IV/)).not.toBeInTheDocument();
  });

  it("draws one smile when two roots list the same strike and right (third Friday: AM monthly and PM weekly), keeping the tighter quote", async () => {
    const am = { ...contract("SPX261016C07550000", 550, "2026-08-07", "call", 0.13), bid: 41.6, ask: 47.5 };
    const pm = { ...contract("SPXW261016C07550000", 550, "2026-08-07", "call", 0.175), bid: 39.7, ask: 39.8 };
    const put = contract("SPXW261016P07550000", 550, "2026-08-07", "put", 0.19);
    apiMocks.getOptionChain.mockResolvedValue(chain([am, pm, put]));
    renderWithLanguage(<VolatilitySmile symbol="SPX" marketPrice={551} />);

    expect(await screen.findByLabelText("call strike 550, IV 17.50%")).toBeInTheDocument();
    expect(screen.queryByLabelText("call strike 550, IV 13.00%")).not.toBeInTheDocument();
    expect(screen.getAllByLabelText(/^call strike 550/)).toHaveLength(1);
  });

  it("leaves IV = 0 contracts off the plot and says how many, instead of drawing them at the edge", async () => {
    const withZeroes = [
      contract("SPY260807C00545000", 545, "2026-08-07", "call", 0.22),
      contract("SPY260807P00550000", 550, "2026-08-07", "put", 0.19),
      contract("SPY260807C00548000", 548, "2026-08-07", "call", 0),
      contract("SPY260807P00549000", 549, "2026-08-07", "put", 0),
      contract("SPY260807P00600000", 600, "2026-08-07", "put", 0),
    ];
    apiMocks.getOptionChain.mockResolvedValue(chain(withZeroes));
    renderWithLanguage(<VolatilitySmile symbol="SPY" marketPrice={551} />);

    expect(await screen.findByLabelText("call strike 545, IV 22.00%")).toBeInTheDocument();
    expect(screen.queryByLabelText("call strike 548, IV 0.00%")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("put strike 549, IV 0.00%")).not.toBeInTheDocument();
    // Two inside the window; the IV = 0 put at 600 is outside it and is not counted.
    expect(screen.getByText("2 contratos dentro del dinero sin IV")).toBeInTheDocument();
  });

  it("puts the strike on the vertical axis and the IV on the horizontal one", async () => {
    renderWithLanguage(<VolatilitySmile symbol="SPY" marketPrice={551} />);

    const high = await screen.findByLabelText("call strike 555, IV 21.00%");
    const low = screen.getByLabelText("call strike 545, IV 22.00%");
    // Higher strike -> smaller cy (higher on screen); higher IV -> larger cx (further right).
    expect(Number(high.getAttribute("cy"))).toBeLessThan(Number(low.getAttribute("cy")));
    expect(Number(low.getAttribute("cx"))).toBeGreaterThan(Number(high.getAttribute("cx")));
  });
});

function withinOptions(selector: HTMLElement): string[] {
  return Array.from(selector.querySelectorAll("option"), (option) => option.value);
}
