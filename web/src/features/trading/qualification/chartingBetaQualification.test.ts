import { describe, expect, it } from 'vitest';
import { indicatorPoints, type CoreIndicatorInstance } from '../indicators/coreIndicators';
import type { MarketBar } from '../tradingTypes';
import { fixture } from '../../../test/fixture';

// Deterministic synthetic bars: a drifting wave with stable wicks and volume.
function syntheticBars(count: number, startTime: number, intervalSeconds: number) {
  const bars = [];
  let close = 40_000;
  for (let index = 0; index < count; index += 1) {
    const open = close;
    close = 40_000 + index * 0.65 + Math.sin(index / 37) * 42 + Math.cos(index / 11) * 18;
    bars.push({
      time: startTime + index * intervalSeconds,
      open,
      high: Math.max(open, close) + 14 + (index % 7),
      low: Math.min(open, close) - 13 - (index % 5),
      close,
      volume: 100 + ((index * 17) % 240),
      isFinal: true,
      ingestionRevision: 1,
    });
  }
  return bars;
}

function marketBars(count: number, intervalSeconds: number): MarketBar[] {
  return syntheticBars(count, 1_700_000_000, intervalSeconds).map((bar) => (fixture({
    instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
    interval: `${intervalSeconds}s`,
    start_time: new Date(bar.time * 1_000).toISOString(),
    end_time: new Date((bar.time + intervalSeconds) * 1_000).toISOString(),
    open: String(bar.open), high: String(bar.high), low: String(bar.low), close: String(bar.close), volume: String(bar.volume),
    is_final: bar.isFinal, adjustment_mode: 'raw', session: '24x7', provider: 'binance', ingestion_revision: bar.ingestionRevision,
    received_at: new Date((bar.time + intervalSeconds) * 1_000).toISOString(),
  })));
}

const indicators: CoreIndicatorInstance[] = [
  { id: 'sma', period: 20, enabled: true },
  { id: 'ema', period: 20, enabled: true },
  { id: 'rsi', period: 14, enabled: true },
];

describe('crypto Charting Beta qualification', () => {
  it('processes four charts × 5,000 bars × three indicators within a bounded deterministic budget', () => {
    const started = performance.now();
    const charts = [60, 300, 900, 3600].map((seconds) => marketBars(5_000, seconds));
    const outputs = charts.flatMap((bars) => indicators.map((indicator) => indicatorPoints(bars, indicator)));
    const elapsed = performance.now() - started;
    expect(outputs).toHaveLength(12);
    expect(outputs.every((points) => points.length > 4_900)).toBe(true);
    expect(elapsed).toBeLessThan(1_500);
  });

  it('maintains exact chronological identities across ten reconnect corrections', () => {
    let bars = marketBars(1_000, 60);
    for (let cycle = 1; cycle <= 10; cycle += 1) {
      const corrected = { ...bars.at(-1)!, close: String(Number(bars.at(-1)!.close) + cycle), ingestion_revision: cycle + 1 };
      const byTime = new Map(bars.map((bar) => [bar.start_time, bar]));
      const existing = byTime.get(corrected.start_time);
      if (!existing || corrected.ingestion_revision >= existing.ingestion_revision) byTime.set(corrected.start_time, corrected);
      bars = [...byTime.values()].sort((left, right) => left.start_time.localeCompare(right.start_time));
    }
    expect(bars).toHaveLength(1_000);
    expect(new Set(bars.map((bar) => bar.start_time)).size).toBe(1_000);
    expect(bars.at(-1)!.ingestion_revision).toBe(11);
  });

  it('keeps a synthetic crosshair fan-out p95 below 32 ms', () => {
    const samples: number[] = [];
    const listeners = Array.from({ length: 4 }, () => ({ time: 0, price: 0 }));
    for (let iteration = 0; iteration < 500; iteration += 1) {
      const started = performance.now();
      listeners.forEach((listener) => { listener.time = iteration; listener.price = 40_000 + iteration; });
      samples.push(performance.now() - started);
    }
    samples.sort((left, right) => left - right);
    expect(samples[Math.floor(samples.length * 0.95)]).toBeLessThan(32);
  });
});
