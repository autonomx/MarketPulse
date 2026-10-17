import { describe, expect, it } from 'vitest';
import type { CoreIndicatorId, CoreIndicatorInstance } from './coreIndicators';
import { allIndicatorPineSources, indicatorPineSource } from './indicatorPine';

const indicatorIds: CoreIndicatorId[] = [
  'sma', 'ema', 'rsi', 'macd', 'bollinger', 'atr', 'vwap', 'bull-market-band',
  'death-cross', 'ema-stack', 'fair-value-gap', 'golden-cross', 'ideal-bb',
  'log-macd', 'macd-dema', 'rsi-divergence', 'stochastic-rsi', 'swing-liquidity',
  'volume-profile',
];

function instance(id: CoreIndicatorId): CoreIndicatorInstance {
  return { id, period: 14, enabled: true, fastPeriod: 8, slowPeriod: 21, signalPeriod: 5 };
}

describe('indicator Pine sources', () => {
  it('provides a source for every built-in indicator', () => {
    const sources = allIndicatorPineSources();
    for (const id of indicatorIds) {
      expect(sources[id]).toContain('//@version=6');
      expect(indicatorPineSource(instance(id))).not.toContain('{{');
    }
  });

  it('uses the configured indicator inputs in the source', () => {
    const source = indicatorPineSource(instance('macd'));
    expect(source).toContain('input.int(8, "Fast Length"');
    expect(source).toContain('input.int(21, "Slow Length"');
    expect(source).toContain('input.int(5, "Signal Smoothing"');
  });
});
