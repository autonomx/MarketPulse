import { afterEach, describe, expect, it, vi } from 'vitest';
import type { MarketBar } from '../tradingTypes';
import type { CoreIndicatorId, CoreIndicatorInstance } from './coreIndicators';
import {
  EXTERNAL_INDICATOR_IDS,
  calculateExternalIndicatorOutputs,
  externalIndicatorAvailableForInstrument,
  externalIndicatorDefinition,
} from './externalIndicatorData';

function bars(instrumentId: string, interval = '1h'): MarketBar[] {
  return [0, 1].map((index) => ({
    instrument_id: instrumentId,
    interval,
    start_time: new Date(Date.UTC(2026, 7, 24, 12 + index)).toISOString(),
    end_time: new Date(Date.UTC(2026, 7, 24, 13 + index)).toISOString(),
    open: '100',
    high: '102',
    low: '99',
    close: '101',
    volume: '10',
    is_final: true,
    adjustment_mode: 'raw',
    session: 'regular',
    provider: 'fixture',
    provider_event_id: null,
    provider_sequence: null,
    ingestion_revision: 1,
    received_at: new Date(Date.UTC(2026, 7, 24, 14)).toISOString(),
  })) as MarketBar[];
}

function indicator(id: string): CoreIndicatorInstance {
  return {
    id: id as CoreIndicatorId,
    period: 20,
    enabled: true,
    visible: true,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('external TradingView indicator data', () => {
  it('maps the requested derivative, analyst, and on-chain families', () => {
    expect(EXTERNAL_INDICATOR_IDS).toContain('tv-open-interest');
    expect(EXTERNAL_INDICATOR_IDS).toContain('tv-funding-rate-a-guide-to-market-sentiment');
    expect(EXTERNAL_INDICATOR_IDS).toContain('tv-liquidation-data-what-to-watch-and-why-it-matters');
    expect(EXTERNAL_INDICATOR_IDS).toContain('tv-analyst-price-forecast');
    expect(EXTERNAL_INDICATOR_IDS).toContain('tv-dividend-yield');
    expect(EXTERNAL_INDICATOR_IDS).toContain('tv-hash-rate');
    expect(externalIndicatorDefinition('tv-open-interest')?.metric).toBe('binance.open_interest');
  });

  it('enforces provider/instrument scope instead of fabricating cross-market data', () => {
    expect(externalIndicatorAvailableForInstrument('tv-open-interest', 'crypto:BINANCE:spot:BTC-USDT')).toBe(true);
    expect(externalIndicatorAvailableForInstrument('tv-open-interest', 'equity:NASDAQ:NVDA')).toBe(false);
    expect(externalIndicatorAvailableForInstrument('tv-analyst-price-forecast', 'equity:NASDAQ:NVDA')).toBe(true);
    expect(externalIndicatorAvailableForInstrument('tv-hash-rate', 'crypto:BINANCE:spot:BTC-USDT')).toBe(true);
    expect(externalIndicatorAvailableForInstrument('tv-hash-rate', 'crypto:BINANCE:spot:ETH-USDT')).toBe(false);
  });

  it('converts provider metric series into native chart outputs', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({
        instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
        metric: 'binance.open_interest',
        provider: 'binance-futures',
        interval: '5m',
        series: [{
          key: 'open-interest',
          title: 'Open Interest',
          unit: 'contracts',
          kind: 'line',
          points: [
            { time: '2026-08-24T12:00:00Z', value: '10' },
            { time: '2026-08-24T13:00:00Z', value: '11.5' },
          ],
        }],
        received_at: '2026-08-24T14:00:00Z',
        freshness_mode: 'polled',
        history_complete: false,
        metadata: {},
      })));

    const outputs = await calculateExternalIndicatorOutputs(
      bars('crypto:BINANCE:spot:BTC-USDT'),
      indicator('tv-open-interest'),
    );

    expect(outputs).toHaveLength(1);
    expect(outputs[0]).toMatchObject({
      key: 'tv-open-interest:open-interest',
      title: 'Open Interest · contracts',
      pane: 1,
      kind: 'line',
    });
    expect(outputs[0].points.map((point) => point.value)).toEqual([10, 11.5]);
  });

  it('extends a current analyst snapshot across the loaded chart range', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({
        instrument_id: 'equity:NASDAQ:NVDA',
        metric: 'yahoo.analyst_price_forecast',
        provider: 'yahoo',
        interval: '1d',
        series: [{
          key: 'target-mean',
          title: 'Analyst Target Mean',
          unit: 'price',
          kind: 'line',
          points: [{ time: '2026-08-24T14:00:00Z', value: '250' }],
        }],
        received_at: '2026-08-24T14:00:00Z',
        freshness_mode: 'polled',
        history_complete: false,
        metadata: { snapshot_only: true },
      })));

    const sourceBars = bars('equity:NASDAQ:NVDA', '1d');
    const outputs = await calculateExternalIndicatorOutputs(
      sourceBars,
      indicator('tv-analyst-price-forecast'),
    );

    expect(outputs[0].pane).toBe(0);
    expect(outputs[0].points).toEqual([
      { time: sourceBars[0].start_time, value: 250 },
      { time: sourceBars[1].end_time, value: 250 },
    ]);
  });
});
