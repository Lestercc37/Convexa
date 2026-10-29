"use client";

import { useEffect, useRef, useState } from "react";

import { EMA_WIDTHS, addEma, EMA_MAX_LINES, EMA_MAX_PERIOD, EMA_MIN_PERIOD, type EmaConfig } from "@/lib/ema";
import { useLanguage } from "@/lib/i18n/language-context";

type EmaMenuProps = {
  emas: EmaConfig[];
  /** Ids of lines whose period is longer than the bars available (still converging from their first value). */
  convergingIds: ReadonlySet<string>;
  onChange: (emas: EmaConfig[]) => void;
};

// The "EMAs" sub-menu of the chart: type any period to add a line, a checkbox shows or hides it, a colour picker changes its colour, the cross removes it. Starts empty: nothing is fixed.
export function EmaMenu({ emas, convergingIds, onChange }: EmaMenuProps) {
  const { t } = useLanguage();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<"invalid" | "duplicate" | "max" | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const submit = () => {
    const result = addEma(emas, draft);
    setError(result.error);
    if (result.error === null) {
      onChange(result.emas);
      setDraft("");
    }
  };

  const labels = t.priceChart;
  const visibleCount = emas.filter((ema) => ema.visible).length;
  const errorText =
    error === "invalid" ? labels.emaInvalid(EMA_MIN_PERIOD, EMA_MAX_PERIOD) : error === "duplicate" ? labels.emaDuplicate : error === "max" ? labels.emaMax(EMA_MAX_LINES) : null;

  return (
    <div className="ema-menu" ref={rootRef}>
      <button
        type="button"
        className="chart-toggle ema-menu-button"
        aria-haspopup="dialog"
        aria-expanded={open}
        title={labels.emaTitle}
        onClick={() => setOpen((value) => !value)}
      >
        {labels.emaButton}
        {emas.length > 0 ? ` (${visibleCount}/${emas.length})` : ""}
      </button>
      {open && (
        <div className="ema-menu-panel" role="dialog" aria-label={labels.emaDialogLabel}>
          <form
            className="ema-menu-add"
            onSubmit={(event) => {
              event.preventDefault();
              submit();
            }}
          >
            <input
              type="text"
              inputMode="numeric"
              aria-label={labels.emaPeriodLabel}
              placeholder={labels.emaPlaceholder}
              value={draft}
              maxLength={4}
              onChange={(event) => {
                setDraft(event.target.value);
                setError(null);
              }}
            />
            <button type="submit">{labels.emaAdd}</button>
          </form>
          {errorText && (
            <p className="ema-menu-error" role="alert">
              {errorText}
            </p>
          )}
          {emas.length === 0 ? (
            <p className="ema-menu-empty">{labels.emaEmpty}</p>
          ) : (
            <ul className="ema-menu-list">
              {emas.map((ema) => (
                <li key={ema.id}>
                  <label className="ema-menu-row">
                    <input
                      type="checkbox"
                      checked={ema.visible}
                      aria-label={labels.emaShow(ema.period)}
                      onChange={(event) => onChange(emas.map((item) => (item.id === ema.id ? { ...item, visible: event.target.checked } : item)))}
                    />
                    <span>EMA {ema.period}</span>
                    {ema.visible && convergingIds.has(ema.id) && (
                      <span className="ema-menu-warming" title={labels.emaConverging}>
                        ~
                      </span>
                    )}
                  </label>
                  <input
                    type="color"
                    value={ema.color}
                    aria-label={labels.emaColor(ema.period)}
                    onChange={(event) => onChange(emas.map((item) => (item.id === ema.id ? { ...item, color: event.target.value } : item)))}
                  />
                  <select
                    className="ema-menu-width"
                    value={ema.width ?? 1}
                    aria-label={labels.emaWidth(ema.period)}
                    onChange={(event) => onChange(emas.map((item) => (item.id === ema.id ? { ...item, width: Number(event.target.value) } : item)))}
                  >
                    {EMA_WIDTHS.map((width) => (
                      <option key={width} value={width}>
                        {labels.emaWidthOption(width)}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="ema-menu-remove"
                    aria-label={labels.emaRemove(ema.period)}
                    onClick={() => onChange(emas.filter((item) => item.id !== ema.id))}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
