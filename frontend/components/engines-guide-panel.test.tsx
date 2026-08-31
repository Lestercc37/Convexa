import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import { ENGINES_REFERENCE } from "@/lib/engines-reference";
import { es } from "@/lib/i18n/es";
import { EnginesGuidePanel } from "./engines-guide-panel";

describe("EnginesGuidePanel", () => {
  it("renders every engine from ENGINES_REFERENCE, with its name and classification badge", () => {
    renderWithLanguage(<EnginesGuidePanel onClose={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "Guía de Interpretación de los Motores" })).toBeInTheDocument();

    for (const entry of ENGINES_REFERENCE) {
      const content = es.enginesGuide.engines[entry.id];
      expect(content).toBeDefined();
      expect(screen.getByRole("heading", { name: content.name })).toBeInTheDocument();
      expect(screen.getByText(content.description)).toBeInTheDocument();
    }

    // At least one of each classification actually renders visibly —
    // proof the badge text isn't just present in the data, but wired
    // through to the two distinct labels.
    expect(screen.getAllByText("🟢 Estándar validado").length).toBeGreaterThan(0);
    expect(screen.getAllByText("🟡 Métrica propia de Convexa").length).toBeGreaterThan(0);
  });

  it("shows the academic citation only for engines that have one", () => {
    renderWithLanguage(<EnginesGuidePanel onClose={vi.fn()} />);

    const withCitation = ENGINES_REFERENCE.filter((entry) => entry.citation);
    const withoutCitation = ENGINES_REFERENCE.filter((entry) => !entry.citation);
    expect(withCitation.length).toBeGreaterThan(0);
    expect(withoutCitation.length).toBeGreaterThan(0);

    const citationCounts = new Map<string, number>();
    for (const entry of withCitation) {
      const text = `Fuente académica: ${entry.citation}`;
      citationCounts.set(text, (citationCounts.get(text) ?? 0) + 1);
    }
    for (const [text, count] of citationCounts) {
      expect(screen.getAllByText(text)).toHaveLength(count);
    }

    const bvcCard = es.enginesGuide.engines.gammaExposure;
    expect(
      screen.queryByText(new RegExp(`Fuente académica.*${bvcCard.name}`)),
    ).not.toBeInTheDocument();
  });

  it("closes via the close button, overlay click, and Escape", async () => {
    const user = userEvent.setup();
    const onCloseButton = vi.fn();
    const { unmount } = renderWithLanguage(<EnginesGuidePanel onClose={onCloseButton} />);

    await user.click(screen.getByRole("button", { name: "Cerrar" }));
    expect(onCloseButton).toHaveBeenCalledTimes(1);
    unmount();

    const onCloseOverlay = vi.fn();
    const { container, unmount: unmountOverlay } = renderWithLanguage(
      <EnginesGuidePanel onClose={onCloseOverlay} />,
    );
    const overlay = container.querySelector(".modal-overlay");
    expect(overlay).not.toBeNull();
    if (overlay) await user.click(overlay);
    expect(onCloseOverlay).toHaveBeenCalledTimes(1);
    unmountOverlay();

    const onCloseEscape = vi.fn();
    renderWithLanguage(<EnginesGuidePanel onClose={onCloseEscape} />);
    await user.keyboard("{Escape}");
    expect(onCloseEscape).toHaveBeenCalledTimes(1);
  });

  it("clicking inside the modal itself does not close it", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithLanguage(<EnginesGuidePanel onClose={onClose} />);

    await user.click(screen.getByRole("heading", { name: "Guía de Interpretación de los Motores" }));
    expect(onClose).not.toHaveBeenCalled();
  });
});
