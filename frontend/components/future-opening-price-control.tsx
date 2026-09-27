"use client";

import { useEffect, useState, type FormEvent } from "react";
import { getFutureOpeningPrice, setFutureOpeningPrice } from "@/lib/api";
import { useLanguage } from "@/lib/i18n/language-context";

type FutureOpeningPriceControlProps = {
  symbol: string;
  onSaved: () => void;
};

// ES/NQ have no working ThetaData price stream/OHLC/EOD endpoint at all
// (see PRICE_PROXY_SYMBOL_BY_FUTURE's own docstring, backend/domain/
// use_cases/read_models.py) -- their chart/VWAP are reconstructed from
// their cash-index proxy's own real price history (SPX/NDX), shifted by
// one constant offset anchored to the owner's own 9:30 ET opening print
// (read off their live futures feed). This control is that one input,
// re-entered once each session -- see futures.py's own endpoint.
export function FutureOpeningPriceControl({ symbol, onSaved }: FutureOpeningPriceControlProps) {
  const { t } = useLanguage();
  const [proxySymbol, setProxySymbol] = useState<string | null>(null);
  const [value, setValue] = useState("");
  const [isSaved, setIsSaved] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setValue("");
    setIsSaved(false);
    setSaveFailed(false);
    getFutureOpeningPrice(symbol, controller.signal)
      .then((response) => {
        setProxySymbol(response.proxy_symbol);
        if (response.opening_price !== null) {
          setValue(String(response.opening_price));
          setIsSaved(true);
        }
      })
      // Unknown/misconfigured symbol -- leave proxySymbol null, the
      // control below just won't have a note to show.
      .catch(() => {});
    return () => controller.abort();
  }, [symbol]);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed <= 0) return;
    setIsSaving(true);
    setSaveFailed(false);
    setFutureOpeningPrice(symbol, parsed)
      .then((response) => {
        setProxySymbol(response.proxy_symbol);
        setIsSaved(true);
        onSaved();
      })
      .catch(() => setSaveFailed(true))
      .finally(() => setIsSaving(false));
  };

  return (
    <form className="tv-future-opening-price" onSubmit={handleSubmit}>
      <label>
        <span className="sr-only">{t.futureOpeningPrice.label}</span>
        <input
          type="number"
          step="0.01"
          min="0"
          inputMode="decimal"
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
            setIsSaved(false);
          }}
          placeholder={t.futureOpeningPrice.label}
        />
      </label>
      <button type="submit" disabled={isSaving || !value}>
        {isSaving ? t.futureOpeningPrice.savingButton : t.futureOpeningPrice.saveButton}
      </button>
      {saveFailed ? (
        <span className="tv-future-opening-price-error" role="alert">
          {t.futureOpeningPrice.saveErrorNote}
        </span>
      ) : proxySymbol ? (
        <span className="tv-future-opening-price-note">
          {isSaved
            ? t.futureOpeningPrice.calibratedNote(proxySymbol)
            : t.futureOpeningPrice.notSetNote(proxySymbol)}
        </span>
      ) : null}
    </form>
  );
}
