"use client";

import { useEffect } from "react";
import { useLanguage } from "@/lib/i18n/language-context";
import { ENGINES_REFERENCE } from "@/lib/engines-reference";

type EnginesGuidePanelProps = {
  onClose: () => void;
};

export function EnginesGuidePanel({ onClose }: EnginesGuidePanelProps) {
  const { t } = useLanguage();

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div className="modal-overlay" role="presentation" onClick={onClose}>
      <div
        className="modal panel engines-guide-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="engines-guide-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="panel-heading engines-guide-heading">
          <div>
            <p className="eyebrow">{t.enginesGuide.eyebrow}</p>
            <h2 id="engines-guide-title">{t.enginesGuide.title}</h2>
            <p className="engines-guide-description">{t.enginesGuide.description}</p>
          </div>
          <button
            type="button"
            className="modal-close"
            aria-label={t.enginesGuide.closeButtonAriaLabel}
            onClick={onClose}
          >
            ×
          </button>
        </div>

        <ul className="engines-guide-list">
          {ENGINES_REFERENCE.map((entry) => {
            const content = t.enginesGuide.engines[entry.id];
            if (!content) return null;
            return (
              <li key={entry.id} className="engines-guide-card">
                <div className="engines-guide-card-heading">
                  <h3>{content.name}</h3>
                  <span
                    className={`engines-guide-badge ${entry.classification}`}
                  >
                    {entry.classification === "standard"
                      ? t.enginesGuide.standardBadge
                      : t.enginesGuide.proprietaryBadge}
                  </span>
                </div>
                <p className="engines-guide-card-description">{content.description}</p>
                {entry.citation && (
                  <p className="engines-guide-card-citation">
                    {t.enginesGuide.citationLabel}: {entry.citation}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
