import type { ExpectedMove } from "@/lib/types";

type ExpectedMoveWidgetProps = {
  expectedMove?: ExpectedMove;
};

const dollars = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const decimal = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function ExpectedMoveWidget({ expectedMove }: ExpectedMoveWidgetProps) {
  if (!expectedMove) return null;

  return (
    <section className="panel expected-move" aria-label="Movimiento esperado">
      <p className="expected-move-primary">
        Movimiento esperado: ±{dollars.format(expectedMove.implied_1sd_dollars)} (
        {decimal.format(expectedMove.implied_1sd_pct)}%) — Rango:{" "}
        {dollars.format(expectedMove.lower_bound)} – {dollars.format(expectedMove.upper_bound)}
      </p>
      <p className="expected-move-remaining">
        Remanente del día: ±{dollars.format(expectedMove.remaining_1sd_dollars)} (
        {decimal.format(expectedMove.remaining_1sd_pct)}%)
      </p>
    </section>
  );
}
