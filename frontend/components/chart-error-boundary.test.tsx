import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChartErrorBoundary } from "./chart-error-boundary";

let broken = false;
function Flaky() {
  if (broken) throw new Error("Value is null");
  return <p>chart</p>;
}

afterEach(() => vi.useRealTimers());

describe("ChartErrorBoundary", () => {
  it("remounts only the chart a second after a library error", () => {
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    broken = true;
    render(<p>dashboard</p>);
    render(
      <ChartErrorBoundary label="falló" retryLabel="otra vez">
        <Flaky />
      </ChartErrorBoundary>,
    );
    expect(screen.queryByText("chart")).not.toBeInTheDocument();
    expect(screen.getByText("dashboard")).toBeInTheDocument();
    broken = false;
    act(() => vi.advanceTimersByTime(1_000));
    expect(screen.getByText("chart")).toBeInTheDocument();
  });

  it("stops retrying after five failures in a minute and offers a button", () => {
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    broken = true;
    render(
      <ChartErrorBoundary label="falló" retryLabel="otra vez">
        <Flaky />
      </ChartErrorBoundary>,
    );
    for (let i = 0; i < 5; i += 1) act(() => vi.advanceTimersByTime(1_000));
    expect(screen.getByRole("alert")).toHaveTextContent("falló");
    broken = false;
    act(() => screen.getByText("otra vez").click());
    expect(screen.getByText("chart")).toBeInTheDocument();
  });
});
