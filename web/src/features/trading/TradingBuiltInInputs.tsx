import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { TradingCompareSymbolDialog } from './TradingCompareSymbolDialog';
import type { CoreIndicatorInstance } from './indicators/coreIndicators';
import { tradingViewBuiltInInputs, tradingViewBuiltInUsesCompareSeries } from './indicators/tradingViewBuiltIns';
import { tradingApi } from './tradingApi';

type Draft = CoreIndicatorInstance;

/** Whether a compare symbol is in the instrument catalog; undefined while checking or when there is none. */
function useCompareSymbolKnown(symbol: string | null | undefined): boolean | undefined {
  const lookup = useQuery({
    queryKey: ['trading', 'compare-instruments', symbol ?? ''],
    queryFn: () => tradingApi.instruments(symbol ?? ''),
    enabled: Boolean(symbol),
    staleTime: 30_000,
  });
  if (!symbol) return undefined;
  if (lookup.isError) return false;
  if (!lookup.data) return undefined;
  return lookup.data.some((instrument) => instrument.instrument_id === symbol);
}

/**
 * The declared inputs of a TradingView built-in (TVP-6.1): the period under its own label, params as number or select fields,
 * and the compare symbol, chosen with the compare-symbol dialog. An unknown compare symbol shows an error and is reported invalid.
 */
export function TradingBuiltInInputs({
  draft,
  currentInstrumentId,
  setDraft,
  onValidityChange,
}: {
  draft: Draft;
  currentInstrumentId: string;
  setDraft: (update: (current: Draft) => Draft) => void;
  onValidityChange: (valid: boolean) => void;
}) {
  const id = String(draft.id);
  const inputs = tradingViewBuiltInInputs(id);
  const usesCompare = tradingViewBuiltInUsesCompareSeries(id);
  const [choosing, setChoosing] = useState(false);
  const known = useCompareSymbolKnown(usesCompare ? draft.compareSymbol : null);
  const invalid = usesCompare && known === false;
  useEffect(() => { onValidityChange(!invalid); }, [invalid, onValidityChange]);
  if (!inputs) return null;

  const setParam = (key: string, value: number | string) => setDraft((current) => ({ ...current, params: { ...current.params, [key]: value } }));
  return (
    <>
      {inputs.periodLabel ? (
        <label className="trading-indicator-settings-field">
          <span>{inputs.periodLabel}</span>
          <input type="number" min={1} step={1} value={draft.period} onChange={(event) => {
            const next = Number(event.target.value);
            if (Number.isFinite(next)) setDraft((current) => ({ ...current, period: Math.max(1, Math.round(next)) }));
          }} />
        </label>
      ) : null}
      {inputs.params.map((spec) => {
        const value = draft.params?.[spec.key];
        if (spec.kind === 'select') {
          return (
            <label key={spec.key} className="trading-indicator-settings-field">
              <span>{spec.label}</span>
              <select value={typeof value === 'string' ? value : spec.default} onChange={(event) => setParam(spec.key, event.target.value)}>
                {spec.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
          );
        }
        return (
          <label key={spec.key} className="trading-indicator-settings-field">
            <span>{spec.label}</span>
            <input type="number" min={spec.min} step={spec.step ?? 1} value={typeof value === 'number' ? value : spec.default} onChange={(event) => {
              const next = Number(event.target.value);
              if (!Number.isFinite(next)) return;
              const bounded = Math.max(spec.min, next);
              setParam(spec.key, spec.integer ? Math.round(bounded) : bounded);
            }} />
          </label>
        );
      })}
      {usesCompare ? (
        <>
          <div className="trading-indicator-settings-field">
            <span>Symbol</span>
            <button type="button" className="trading-indicator-settings-symbol" onClick={() => setChoosing(true)}>
              {draft.compareSymbol || 'Choose symbol…'}
            </button>
          </div>
          {invalid ? (
            <p className="trading-indicator-settings-error" role="alert">{`${draft.compareSymbol} is not in the instrument catalog. Choose another symbol.`}</p>
          ) : (
            <p className="trading-indicator-settings-help">The symbol to correlate with, loaded on the chart's interval like a compare symbol.</p>
          )}
          <TradingCompareSymbolDialog
            open={choosing}
            mode="choose"
            currentInstrumentId={currentInstrumentId}
            existingInstrumentIds={[]}
            onChoose={(instrument) => setDraft((current) => ({ ...current, compareSymbol: instrument.instrument_id }))}
            onClose={() => setChoosing(false)}
          />
        </>
      ) : null}
    </>
  );
}
