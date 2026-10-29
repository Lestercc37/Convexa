"use client";

// The price chart heals itself (Lester 2026-10-29: "Value is null" from lightweight-charts' own renderer, once in the EMA lines 30 s after a
// symbol switch, once in the candles 14 minutes into a session, each time taking the whole dashboard down). Whatever the library throws inside
// the chart, only the chart is remounted, a second later, and the rest of the dashboard stays on screen. The error is kept in the browser
// (lib/crash-log.ts) as before. After MAX_RETRIES remounts within a minute it stops retrying and shows a small notice with a button.

import { Component, type ReactNode } from "react";
import { recordCrash } from "@/lib/crash-log";
import { recordChartEvent } from "@/lib/chart-lifecycle";

const RETRY_DELAY_MS = 1_000;
const MAX_RETRIES = 5;
const RETRY_WINDOW_MS = 60_000;

type Props = { children: ReactNode; label: string; retryLabel: string };
type State = { failed: boolean; generation: number; gaveUp: boolean };

export class ChartErrorBoundary extends Component<Props, State> {
  state: State = { failed: false, generation: 0, gaveUp: false };
  private retries: number[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;

  static getDerivedStateFromError(): Partial<State> {
    return { failed: true };
  }

  componentDidCatch(error: Error): void {
    recordCrash(error);
    const now = Date.now();
    this.retries = this.retries.filter((at) => now - at < RETRY_WINDOW_MS);
    recordChartEvent("chart-self-heal", { message: String(error?.message ?? error).slice(0, 120), retries: this.retries.length });
    if (this.retries.length >= MAX_RETRIES) {
      this.setState({ gaveUp: true });
      return;
    }
    this.retries.push(now);
    this.timer = setTimeout(() => this.setState((state) => ({ failed: false, generation: state.generation + 1 })), RETRY_DELAY_MS);
  }

  componentWillUnmount(): void {
    if (this.timer) clearTimeout(this.timer);
  }

  render(): ReactNode {
    if (this.state.gaveUp) {
      return (
        <div className="chart-self-heal-notice" role="alert">
          {this.props.label}{" "}
          <button type="button" onClick={() => { this.retries = []; this.setState((state) => ({ failed: false, gaveUp: false, generation: state.generation + 1 })); }}>
            {this.props.retryLabel}
          </button>
        </div>
      );
    }
    if (this.state.failed) return null;
    return <div key={this.state.generation} style={{ display: "contents" }}>{this.props.children}</div>;
  }
}
