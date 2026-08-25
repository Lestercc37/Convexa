"use client";

import { useEffect, useState } from "react";
import { getWhaleThresholds, updateWhaleThreshold } from "@/lib/api";
import { describeError } from "@/lib/i18n/describe-error";
import { useLanguage } from "@/lib/i18n/language-context";
import type { WhaleThreshold } from "@/lib/types";

type WhaleThresholdsPanelProps = {
  onClose: () => void;
};

type FieldKey =
  | "unusual_min"
  | "whale_min"
  | "unusual_multiplier"
  | "whale_multiplier"
  | "sustained_flow_min";

const FIELD_KEYS: FieldKey[] = [
  "unusual_min",
  "whale_min",
  "unusual_multiplier",
  "whale_multiplier",
  "sustained_flow_min",
];

type RowStatus = "idle" | "saving" | "saved" | "error";

type RowState = {
  values: Record<FieldKey, string>;
  status: RowStatus;
  errorMessage: string | null;
};

const HEADER_KEY: Record<
  FieldKey,
  "unusualMin" | "whaleMin" | "unusualMultiplier" | "whaleMultiplier" | "sustainedFlowMin"
> = {
  unusual_min: "unusualMin",
  whale_min: "whaleMin",
  unusual_multiplier: "unusualMultiplier",
  whale_multiplier: "whaleMultiplier",
  sustained_flow_min: "sustainedFlowMin",
};

function toValues(threshold: WhaleThreshold): Record<FieldKey, string> {
  return {
    unusual_min: String(threshold.unusual_min),
    whale_min: String(threshold.whale_min),
    unusual_multiplier: String(threshold.unusual_multiplier),
    whale_multiplier: String(threshold.whale_multiplier),
    sustained_flow_min: String(threshold.sustained_flow_min),
  };
}

export function WhaleThresholdsPanel({ onClose }: WhaleThresholdsPanelProps) {
  const { t } = useLanguage();
  const [rows, setRows] = useState<Record<string, RowState> | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);

  useEffect(() => {
    const controller = new AbortController();
    getWhaleThresholds(controller.signal)
      .then((response) => {
        const next: Record<string, RowState> = {};
        for (const threshold of response.thresholds) {
          next[threshold.symbol] = {
            values: toValues(threshold),
            status: "idle",
            errorMessage: null,
          };
        }
        setRows(next);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setLoadError(reason);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const setField = (symbol: string, field: FieldKey, value: string) => {
    setRows((current) => {
      if (!current) return current;
      const row = current[symbol];
      return {
        ...current,
        [symbol]: {
          ...row,
          values: { ...row.values, [field]: value },
          status: "idle",
          errorMessage: null,
        },
      };
    });
  };

  const save = async (symbol: string) => {
    setRows((current) => {
      if (!current) return current;
      const row = current[symbol];
      const numbers = {
        unusual_min: Number(row.values.unusual_min),
        whale_min: Number(row.values.whale_min),
        unusual_multiplier: Number(row.values.unusual_multiplier),
        whale_multiplier: Number(row.values.whale_multiplier),
        sustained_flow_min: Number(row.values.sustained_flow_min),
      };
      const isValid = Object.values(numbers).every(
        (value) => Number.isFinite(value) && value > 0,
      );
      if (!isValid) {
        return {
          ...current,
          [symbol]: { ...row, status: "error", errorMessage: t.whaleThresholdsPanel.validationError },
        };
      }
      void updateWhaleThreshold(symbol, numbers)
        .then((updated) => {
          setRows((latest) =>
            latest && {
              ...latest,
              [symbol]: { values: toValues(updated), status: "saved", errorMessage: null },
            },
          );
        })
        .catch((reason: unknown) => {
          setRows((latest) =>
            latest && {
              ...latest,
              [symbol]: { ...latest[symbol], status: "error", errorMessage: describeError(reason, t) },
            },
          );
        });
      return { ...current, [symbol]: { ...row, status: "saving", errorMessage: null } };
    });
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={onClose}>
      <div
        className="modal panel whale-thresholds-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="whale-thresholds-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="panel-heading whale-thresholds-heading">
          <div>
            <h2 id="whale-thresholds-title">{t.whaleThresholdsPanel.title}</h2>
            <p className="whale-thresholds-description">{t.whaleThresholdsPanel.description}</p>
          </div>
          <button
            type="button"
            className="modal-close"
            aria-label={t.whaleThresholdsPanel.closeButtonAriaLabel}
            onClick={onClose}
          >
            ×
          </button>
        </div>

        {loadError ? (
          <p className="whale-thresholds-status error" role="alert">
            {describeError(loadError, t)}
          </p>
        ) : rows === null ? (
          <p className="whale-thresholds-status" aria-live="polite">
            {t.whaleThresholdsPanel.loading}
          </p>
        ) : (
          <div className="whale-thresholds-table-wrap">
            <table className="whale-thresholds-table">
              <thead>
                <tr>
                  <th>{t.whaleThresholdsPanel.headers.symbol}</th>
                  <th>{t.whaleThresholdsPanel.headers.unusualMin}</th>
                  <th>{t.whaleThresholdsPanel.headers.whaleMin}</th>
                  <th>{t.whaleThresholdsPanel.headers.unusualMultiplier}</th>
                  <th>{t.whaleThresholdsPanel.headers.whaleMultiplier}</th>
                  <th>{t.whaleThresholdsPanel.headers.sustainedFlowMin}</th>
                  <th>{t.whaleThresholdsPanel.headers.actions}</th>
                </tr>
              </thead>
              <tbody>
                {Object.keys(rows)
                  .sort()
                  .map((symbol) => {
                    const row = rows[symbol];
                    return (
                      <tr key={symbol}>
                        <td className="whale-thresholds-symbol">{symbol}</td>
                        {FIELD_KEYS.map((key) => (
                          <td key={key}>
                            <input
                              type="number"
                              min="0"
                              step="any"
                              value={row.values[key]}
                              onChange={(event) => setField(symbol, key, event.target.value)}
                              aria-label={`${symbol} ${t.whaleThresholdsPanel.headers[HEADER_KEY[key]]}`}
                            />
                          </td>
                        ))}
                        <td className="whale-thresholds-actions">
                          <button
                            type="button"
                            onClick={() => void save(symbol)}
                            disabled={row.status === "saving"}
                          >
                            {row.status === "saving"
                              ? t.whaleThresholdsPanel.savingButton
                              : t.whaleThresholdsPanel.saveButton}
                          </button>
                          {row.status === "saved" && (
                            <span className="whale-thresholds-saved" role="status">
                              {t.whaleThresholdsPanel.savedConfirmation}
                            </span>
                          )}
                          {row.status === "error" && (
                            <span className="whale-thresholds-error" role="alert">
                              {row.errorMessage ?? t.whaleThresholdsPanel.saveFailed}
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
