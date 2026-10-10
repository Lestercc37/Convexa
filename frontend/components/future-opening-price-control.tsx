"use client";

import { useEffect, useId, useState, type FormEvent } from "react";
import { ApiError, getFutureOpeningPrice, setFutureOpeningPrice } from "@/lib/api";
import { useLanguage } from "@/lib/i18n/language-context";
import type { FutureOpeningPriceResponse } from "@/lib/types";

type FutureOpeningPriceControlProps = {
  symbol: string;
  onSaved: () => void;
};

const EASTERN_TIME_ZONE = "America/New_York";

function formatEasternTime(isoTimestamp: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: EASTERN_TIME_ZONE,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(isoTimestamp));
}

// ES/NQ have no working ThetaData price stream/OHLC/EOD endpoint at all
// (see PRICE_PROXY_SYMBOL_BY_FUTURE's own docstring, backend/domain/
// use_cases/read_models.py) -- their chart/VWAP are reconstructed from
// their cash-index proxy's own real price history (SPX/NDX), shifted by
// one constant offset anchored to the owner's own 9:30:00 ET opening print
// (the open of the future's 1-minute candle, read off their live futures feed).
// This control is that one input, re-entered once each session -- see
// futures.py's own endpoint.
//
// Saving is closed until today's session has the index's first price (9:30:02):
// before that the backend would attach the number to the PREVIOUS session, so the
// API refuses and this control says so, and always shows which session the stored
// number belongs to.
export function FutureOpeningPriceControl({ symbol, onSaved }: FutureOpeningPriceControlProps) {
  const { t } = useLanguage();
  const hintId = useId();
  const [info, setInfo] = useState<FutureOpeningPriceResponse | null>(null);
  const [value, setValue] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [saveFailure, setSaveFailure] = useState<"closed" | "failed" | null>(null);

  useEffect(() => {
    // The dashboard remounts this control per symbol (key={symbol}), so the state starts clean.
    const controller = new AbortController();
    getFutureOpeningPrice(symbol, controller.signal)
      .then((response) => {
        setInfo(response);
        if (response.opening_price !== null) setValue(String(response.opening_price));
      })
      // Unknown/misconfigured symbol -- leave info null, the control below
      // just won't have a note to show.
      .catch(() => {});
    return () => controller.abort();
  }, [symbol]);

  const accepting = info?.accepting !== false;
  const proxySymbol = info?.proxy_symbol ?? null;
  const isSaved = info !== null && info.opening_price !== null;

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed <= 0 || !accepting) return;
    setIsSaving(true);
    setSaveFailure(null);
    setFutureOpeningPrice(symbol, parsed)
      .then((response) => {
        setInfo(response);
        onSaved();
      })
      .catch((reason: unknown) => {
        if (reason instanceof ApiError && reason.code === "OPENING_PRICE_NOT_OPEN_YET") {
          setSaveFailure("closed");
          setInfo((current) =>
            current ? { ...current, accepting: false, waiting_reason: "waiting_first_price" } : current,
          );
        } else {
          setSaveFailure("failed");
        }
      })
      .finally(() => setIsSaving(false));
  };

  let note: string | null = null;
  let isError = false;
  if (saveFailure === "failed") {
    note = t.futureOpeningPrice.saveErrorNote;
    isError = true;
  } else if (proxySymbol && (!accepting || saveFailure === "closed")) {
    note =
      info?.waiting_reason === "before_open"
        ? t.futureOpeningPrice.waitingBeforeOpenNote(proxySymbol)
        : t.futureOpeningPrice.waitingFirstPriceNote(proxySymbol);
  } else if (info && isSaved && info.opening_price !== null) {
    note = t.futureOpeningPrice.savedNote(
      info.session_date,
      String(info.opening_price),
      info.saved_at ? formatEasternTime(info.saved_at) : "--:--:--",
    );
  } else if (info) {
    note = t.futureOpeningPrice.notSavedNote(info.session_date);
  }

  const hint = t.futureOpeningPrice.hint(symbol);
  return (
    <form className="tv-future-opening-price" onSubmit={handleSubmit}>
      <div className="tv-future-opening-price-row">
        <label>
          <span className="sr-only">{t.futureOpeningPrice.label}</span>
          <input
            type="number"
            step="0.01"
            min="0"
            inputMode="decimal"
            value={value}
            title={hint}
            aria-describedby={hintId}
            onChange={(event) => {
              setValue(event.target.value);
              setSaveFailure(null);
            }}
            placeholder={t.futureOpeningPrice.label}
          />
        </label>
        <button type="submit" disabled={isSaving || !value || !accepting}>
          {isSaving ? t.futureOpeningPrice.savingButton : t.futureOpeningPrice.saveButton}
        </button>
      </div>
      <span id={hintId} className="tv-future-opening-price-hint">
        {hint}
      </span>
      {note ? (
        isError ? (
          <span className="tv-future-opening-price-error" role="alert">
            {note}
          </span>
        ) : (
          <span className="tv-future-opening-price-note">{note}</span>
        )
      ) : null}
    </form>
  );
}
