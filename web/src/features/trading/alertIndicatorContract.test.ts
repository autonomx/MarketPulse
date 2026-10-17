// The alert indicator contract (TVP-1.3): for each indicator the server evaluates, the inputs a chart's default
// instance sends and the output lines it draws. src/tests/trading/test_trading_alert_indicator_contract.py checks
// these against the server, so a chart line offered in the alert dialog is one the server can alert on.
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { alertIndicatorChoices } from './alertIndicatorSources';
import type { CoreIndicatorId } from './indicators/coreIndicators';
import { indicatorOutputsFor, syntheticBars } from './indicatorOutputKeys';
import { defaultTradingIndicators, newIndicatorInstance } from './tradingStore';
import type { CoreIndicatorInstance } from './indicators/coreIndicators';

// Tests run from the web package directory.
const FIXTURE = resolve(process.cwd(), 'src/features/trading/indicators/fixtures/alertIndicatorContract.json');

describe('alert indicator contract (TVP-1.3)', () => {
  it('records each server indicator a chart offers, with its default inputs and lines', () => {
    const fixture = JSON.parse(readFileSync(FIXTURE, 'utf-8')) as { note: string; ids: string[]; entries: unknown[]; variants?: unknown[] };
    const series = syntheticBars();
    const serverIds = new Set(fixture.ids);
    const entries = fixture.ids.flatMap((id) => {
      let instance;
      try {
        instance = newIndicatorInstance(id as CoreIndicatorId);
      } catch {
        return [];
      }
      // As the chart computes them (indicatorScheduler.ts).
      const outputs = indicatorOutputsFor(instance, series);
      const [choice] = alertIndicatorChoices([instance], outputs, serverIds);
      return choice && !choice.unavailable ? [{ id, inputs: choice.inputs, outputs: choice.outputs.map((output) => output.key) }] : [];
    });
    // The chart's own starting indicators, and some inputs changed from their defaults.
    const variantInstances: CoreIndicatorInstance[] = [
      ...defaultTradingIndicators(),
      { id: 'macd', period: 5, fastPeriod: 5, slowPeriod: 35, signalPeriod: 5, enabled: true },
      { id: 'bollinger', period: 10, standardDeviations: 3, enabled: true },
      { id: 'stochastic-rsi', period: 10, fastPeriod: 5, signalPeriod: 4, enabled: true },
      { id: 'rsi', period: 21, enabled: true },
      { id: 'vwap', period: 1, anchorTime: '2026-01-02T15:00:00.000Z', enabled: true },
      // Params and a compare symbol reach the server (TVP-1.3).
      { id: 'tv-correlation-coefficient-cc', period: 20, compareSymbol: 'equity:NASDAQ:QQQ', enabled: true },
      { id: 'tv-vwap-auto-anchored', period: 100, params: { anchor: 'lowest-low' }, enabled: true },
      { id: 'tv-bollinger-bars', period: 10, params: { deviations: 1.5 }, enabled: true },
      { id: 'tv-multi-time-period-charts-indicator', period: 1, params: { period: 'W' }, enabled: true },
    ] as CoreIndicatorInstance[];
    const variants = variantInstances.flatMap((instance) => {
      const outputs = indicatorOutputsFor(instance, series);
      const [choice] = alertIndicatorChoices([instance], outputs, serverIds);
      return choice && !choice.unavailable ? [{ id: instance.id, inputs: choice.inputs, outputs: choice.outputs.map((output) => output.key) }] : [];
    });
    if (process.env.UPDATE_ALERT_CONTRACT) writeFileSync(FIXTURE, `${JSON.stringify({ ...fixture, entries, variants }, null, 1)}\n`);
    const saved = JSON.parse(readFileSync(FIXTURE, 'utf-8'));
    expect(entries).toEqual(saved.entries);
    expect(variants).toEqual(saved.variants);
    expect(variants.length).toBeGreaterThan(4);
    expect(entries.length).toBeGreaterThan(50);
  });
});
