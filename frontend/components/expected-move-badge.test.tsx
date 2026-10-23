import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import type { ExpectedMove } from "@/lib/types";
import { ExpectedMoveCompactBadge } from "./expected-move-badge";

const expectedMove: ExpectedMove = {
  implied_1sd_dollars: 62.7,
  implied_1sd_pct: 0.8213,
  remaining_1sd_dollars: 41.2,
  remaining_1sd_pct: 0.54,
  upper_bound: 7705.04,
  lower_bound: 7579.64,
  atm_iv: 0.1423,
};

describe("ExpectedMoveCompactBadge", () => {
  it("shows the expected move percentage and points, the same 1-sigma move the EMC / EMP lines use", () => {
    renderWithLanguage(<ExpectedMoveCompactBadge expectedMove={expectedMove} />);

    const pill = screen.getByText(/MOV\. ESP\. ±0\.82%/);
    expect(pill).toHaveTextContent("±62.70");
    expect(pill).toHaveAttribute("title", expect.stringContaining("7,579.64"));
    expect(pill).toHaveAttribute("title", expect.stringContaining("7,705.04"));
    expect(pill).toHaveAttribute("title", expect.stringContaining("14.23"));
  });

  it("renders nothing without an expected move or with a zero move", () => {
    const { container } = renderWithLanguage(<ExpectedMoveCompactBadge expectedMove={undefined} />);
    expect(container).toBeEmptyDOMElement();
    const zero = renderWithLanguage(<ExpectedMoveCompactBadge expectedMove={{ ...expectedMove, implied_1sd_pct: 0 }} />);
    expect(zero.container).toBeEmptyDOMElement();
  });
});
