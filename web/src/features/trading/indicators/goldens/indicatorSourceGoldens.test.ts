// @vitest-environment node
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import type { MarketBar } from '../../tradingTypes';
import { indicatorOutputs, type CoreIndicatorId, type CoreIndicatorInstance, type IndicatorOutput } from '../coreIndicators';
import { calculateWithSources, SOURCE_TARGET_IDS } from '../indicatorSources';
import { calculateTradingViewBuiltInOutputs, isTradingViewBuiltInId } from '../tradingViewBuiltIns';
import { ALTERNATIVE_PERIOD_DATASET, asMarketBars, generateGoldenDatasets } from '../../../../test/indicatorGoldenDatasets';

// Indicator on indicator (TVP-6.5), shared with the server (src/tests/trading/test_indicator_sources.py).
// Regenerate after an intended change: UPDATE_INDICATOR_GOLDENS=1 npm --prefix web run test -- indicatorSourceGoldens
const FILE = resolve(dirname(fileURLToPath(import.meta.url)), '../../../../../../resources/trading/indicator_goldens/sources.json');
const UPDATE = process.env.UPDATE_INDICATOR_GOLDENS === '1';

type Inputs = { period: number; fastPeriod?: number; slowPeriod?: number; signalPeriod?: number; standardDeviations?: number };
type Case = { target: { id: string; inputs: Inputs }; source: { id: string; inputs: Inputs; output: number } };

// `source.output` is the index of the source indicator's output the target reads.
const CASES: Case[] = [
  { target: { id: 'sma', inputs: { period: 10 } }, source: { id: 'rsi', inputs: { period: 14 }, output: 0 } },
  { target: { id: 'ema', inputs: { period: 9 } }, source: { id: 'tv-on-balance-volume-obv', inputs: { period: 20 }, output: 0 } },
  { target: { id: 'tv-hull-moving-average', inputs: { period: 9 } }, source: { id: 'macd', inputs: { period: 9, fastPeriod: 12, slowPeriod: 26, signalPeriod: 9 }, output: 1 } },
  { target: { id: 'rsi', inputs: { period: 7 } }, source: { id: 'tv-money-flow-mfi', inputs: { period: 14 }, output: 0 } },
  { target: { id: 'bollinger', inputs: { period: 20, standardDeviations: 2 } }, source: { id: 'stochastic-rsi', inputs: { period: 14 }, output: 0 } },
  { target: { id: 'tv-true-strength-index', inputs: { period: 13, fastPeriod: 13, slowPeriod: 25, signalPeriod: 13 } }, source: { id: 'sma', inputs: { period: 5 }, output: 0 } },
  { target: { id: 'macd', inputs: { period: 9, fastPeriod: 12, slowPeriod: 26, signalPeriod: 9 } }, source: { id: 'tv-commodity-channel-index-cci', inputs: { period: 20 }, output: 0 } },
  { target: { id: 'tv-rate-of-change-roc', inputs: { period: 9 } }, source: { id: 'atr', inputs: { period: 14 }, output: 0 } },
];

function instance(id: string, inputs: Inputs): CoreIndicatorInstance {
  return { id: id as CoreIndicatorId, enabled: true, ...inputs };
}

function raw(bars: readonly MarketBar[], indicator: CoreIndicatorInstance): IndicatorOutput[] {
  const id = String(indicator.id);
  return isTradingViewBuiltInId(id) ? calculateTradingViewBuiltInOutputs(bars, { ...indicator, id }) as IndicatorOutput[] : indicatorOutputs(bars, indicator);
}

function fixture(): string {
  const dataset = generateGoldenDatasets().find((item) => item.name === ALTERNATIVE_PERIOD_DATASET)!;
  const bars = asMarketBars(dataset);
  const indexByTime = new Map(bars.map((bar, index) => [bar.start_time, index]));
  const cases = CASES.map((item) => {
    const source = instance(item.source.id, item.source.inputs);
    const output = raw(bars, source)[item.source.output].key;
    const target = { ...instance(item.target.id, item.target.inputs), source: { indicatorId: item.source.id, output } };
    const outputs = calculateWithSources(bars, [source, target], raw).filter((result) => result.key.split(':', 1)[0] === item.target.id);
    return {
      dataset: dataset.name,
      target: item.target,
      source: { id: item.source.id, inputs: item.source.inputs, output },
      outputs: outputs.map((result) => ({
        key: result.key,
        points: result.points.map((point): [number, number | null] => [indexByTime.get(point.time)!, Number.isFinite(point.value) ? point.value : null]),
      })),
    };
  });
  return `${JSON.stringify({ targets: SOURCE_TARGET_IDS, cases }, null, 1)}\n`;
}

describe('indicator on indicator goldens shared with the server (TVP-6.5)', () => {
  it('matches the stored fixture', () => {
    const content = fixture();
    if (UPDATE) {
      writeFileSync(FILE, content);
      return;
    }
    expect(existsSync(FILE), `${join(FILE)} is missing; regenerate with UPDATE_INDICATOR_GOLDENS=1`).toBe(true);
    expect(readFileSync(FILE, 'utf8').replace(/\r\n/g, '\n')).toBe(content);
  });

  it('has values for every case', () => {
    const parsed = JSON.parse(fixture()) as { cases: Array<{ outputs: Array<{ points: unknown[] }> }> };
    for (const item of parsed.cases) expect(item.outputs.some((output) => output.points.length > 0)).toBe(true);
  });
});
