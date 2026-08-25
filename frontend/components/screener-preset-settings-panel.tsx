"use client";

import { useEffect, useState } from "react";
import { getScreenerPresetSettings, updateScreenerPresetSettings } from "@/lib/api";
import { describeError } from "@/lib/i18n/describe-error";
import { useLanguage } from "@/lib/i18n/language-context";
import type { Translations } from "@/lib/i18n/translations";
import type { ConfigurableScreenerPreset, ScreenerPresetSettings } from "@/lib/types";

type ScreenerPresetSettingsPanelProps = {
  onClose: () => void;
};

const PRESET_ORDER: ConfigurableScreenerPreset[] = [
  "negative-gamma-board",
  "vanna-exposure-leaders",
  "charm-decay-pressure",
];

type RowStatus = "idle" | "saving" | "saved" | "error";

type RowState = {
  netGammaMax: string;
  minMagnitude: string;
  limit: string;
  status: RowStatus;
  errorMessage: string | null;
};

function toRowState(settings: ScreenerPresetSettings): RowState {
  return {
    netGammaMax: settings.net_gamma_max === null ? "" : String(settings.net_gamma_max),
    minMagnitude: settings.min_magnitude === null ? "" : String(settings.min_magnitude),
    limit: settings.limit === null ? "" : String(settings.limit),
    status: "idle",
    errorMessage: null,
  };
}

export function ScreenerPresetSettingsPanel({ onClose }: ScreenerPresetSettingsPanelProps) {
  const { t } = useLanguage();
  const [rows, setRows] = useState<Record<ConfigurableScreenerPreset, RowState> | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);

  useEffect(() => {
    const controller = new AbortController();
    getScreenerPresetSettings(controller.signal)
      .then((response) => {
        const next = {} as Record<ConfigurableScreenerPreset, RowState>;
        for (const settings of response.settings) {
          next[settings.preset] = toRowState(settings);
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

  const setField = (
    preset: ConfigurableScreenerPreset,
    field: "netGammaMax" | "minMagnitude" | "limit",
    value: string,
  ) => {
    setRows((current) => {
      if (!current) return current;
      const row = current[preset];
      return {
        ...current,
        [preset]: { ...row, [field]: value, status: "idle", errorMessage: null },
      };
    });
  };

  const save = async (preset: ConfigurableScreenerPreset) => {
    setRows((current) => {
      if (!current) return current;
      const row = current[preset];

      if (preset === "negative-gamma-board") {
        const netGammaMax = Number(row.netGammaMax);
        if (row.netGammaMax.trim() === "" || !Number.isFinite(netGammaMax)) {
          return {
            ...current,
            [preset]: {
              ...row,
              status: "error",
              errorMessage: t.screenerPresetSettingsPanel.gammaValidationError,
            },
          };
        }
        void updateScreenerPresetSettings(preset, { net_gamma_max: netGammaMax })
          .then((updated) => {
            setRows(
              (latest) =>
                latest && { ...latest, [preset]: { ...toRowState(updated), status: "saved" } },
            );
          })
          .catch((reason: unknown) => {
            setRows(
              (latest) =>
                latest && {
                  ...latest,
                  [preset]: {
                    ...latest[preset],
                    status: "error",
                    errorMessage: describeError(reason, t),
                  },
                },
            );
          });
        return { ...current, [preset]: { ...row, status: "saving", errorMessage: null } };
      }

      const minMagnitude = row.minMagnitude.trim() === "" ? null : Number(row.minMagnitude);
      const limit = row.limit.trim() === "" ? null : Number(row.limit);
      const isValid =
        (minMagnitude === null || (Number.isFinite(minMagnitude) && minMagnitude >= 0)) &&
        (limit === null || (Number.isInteger(limit) && limit >= 1));
      if (!isValid) {
        return {
          ...current,
          [preset]: {
            ...row,
            status: "error",
            errorMessage: t.screenerPresetSettingsPanel.exposureValidationError,
          },
        };
      }
      void updateScreenerPresetSettings(preset, { min_magnitude: minMagnitude, limit })
        .then((updated) => {
          setRows((latest) => latest && { ...latest, [preset]: toRowState(updated) });
        })
        .catch((reason: unknown) => {
          setRows(
            (latest) =>
              latest && {
                ...latest,
                [preset]: {
                  ...latest[preset],
                  status: "error",
                  errorMessage: describeError(reason, t),
                },
              },
          );
        });
      return { ...current, [preset]: { ...row, status: "saving", errorMessage: null } };
    });
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={onClose}>
      <div
        className="modal panel whale-thresholds-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="screener-preset-settings-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="panel-heading whale-thresholds-heading">
          <div>
            <h2 id="screener-preset-settings-title">{t.screenerPresetSettingsPanel.title}</h2>
            <p className="whale-thresholds-description">
              {t.screenerPresetSettingsPanel.description}
            </p>
          </div>
          <button
            type="button"
            className="modal-close"
            aria-label={t.screenerPresetSettingsPanel.closeButtonAriaLabel}
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
            {t.screenerPresetSettingsPanel.loading}
          </p>
        ) : (
          <ScreenerPresetSettingsTable
            rows={rows}
            t={t}
            onFieldChange={setField}
            onSave={(preset) => void save(preset)}
          />
        )}
      </div>
    </div>
  );
}

function ScreenerPresetSettingsTable({
  rows,
  t,
  onFieldChange,
  onSave,
}: {
  rows: Record<ConfigurableScreenerPreset, RowState>;
  t: Translations;
  onFieldChange: (
    preset: ConfigurableScreenerPreset,
    field: "netGammaMax" | "minMagnitude" | "limit",
    value: string,
  ) => void;
  onSave: (preset: ConfigurableScreenerPreset) => void;
}) {
  const headers = t.screenerPresetSettingsPanel.headers;

  return (
    <div className="whale-thresholds-table-wrap">
      <table className="whale-thresholds-table">
        <thead>
          <tr>
            <th>{headers.preset}</th>
            <th>{headers.netGammaMax}</th>
            <th>{headers.minMagnitude}</th>
            <th>{headers.limit}</th>
            <th>{headers.actions}</th>
          </tr>
        </thead>
        <tbody>
          {PRESET_ORDER.map((preset) => {
            const row = rows[preset];
            const isGamma = preset === "negative-gamma-board";
            return (
              <tr key={preset}>
                <td className="whale-thresholds-symbol">
                  {t.screenerPresetSettingsPanel.presetLabel[preset]}
                </td>
                <td>
                  {isGamma ? (
                    <input
                      type="number"
                      step="any"
                      value={row.netGammaMax}
                      onChange={(event) => onFieldChange(preset, "netGammaMax", event.target.value)}
                      aria-label={`${t.screenerPresetSettingsPanel.presetLabel[preset]} ${headers.netGammaMax}`}
                    />
                  ) : (
                    t.screenerPresetSettingsPanel.notApplicable
                  )}
                </td>
                <td>
                  {isGamma ? (
                    t.screenerPresetSettingsPanel.notApplicable
                  ) : (
                    <input
                      type="number"
                      min="0"
                      step="any"
                      value={row.minMagnitude}
                      onChange={(event) => onFieldChange(preset, "minMagnitude", event.target.value)}
                      aria-label={`${t.screenerPresetSettingsPanel.presetLabel[preset]} ${headers.minMagnitude}`}
                    />
                  )}
                </td>
                <td>
                  {isGamma ? (
                    t.screenerPresetSettingsPanel.notApplicable
                  ) : (
                    <input
                      type="number"
                      min="1"
                      step="1"
                      value={row.limit}
                      onChange={(event) => onFieldChange(preset, "limit", event.target.value)}
                      aria-label={`${t.screenerPresetSettingsPanel.presetLabel[preset]} ${headers.limit}`}
                    />
                  )}
                </td>
                <td className="whale-thresholds-actions">
                  <button type="button" onClick={() => onSave(preset)} disabled={row.status === "saving"}>
                    {row.status === "saving"
                      ? t.screenerPresetSettingsPanel.savingButton
                      : t.screenerPresetSettingsPanel.saveButton}
                  </button>
                  {row.status === "saved" && (
                    <span className="whale-thresholds-saved" role="status">
                      {t.screenerPresetSettingsPanel.savedConfirmation}
                    </span>
                  )}
                  {row.status === "error" && (
                    <span className="whale-thresholds-error" role="alert">
                      {row.errorMessage ?? t.screenerPresetSettingsPanel.saveFailed}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
