// The external-data indicator contract (TVP-0.2): each indicator's metric, instruments and output lines, as the chart
// draws them. src/tests/trading/test_trading_external_indicators.py checks the server's table
// (indicators/external.py) against this fixture, so an alert or screen on one of these lines reads the same series.
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { EXTERNAL_INDICATOR_IDS, externalIndicatorDefinition, externalIndicatorOutputKeys } from './externalIndicatorData';

// Tests run from the web package directory.
const FIXTURE = resolve(process.cwd(), 'src/features/trading/indicators/fixtures/externalIndicatorContract.json');

describe('external indicator contract (TVP-0.2)', () => {
  it('records each external indicator with its metric, scope and outputs', () => {
    const entries = [...EXTERNAL_INDICATOR_IDS].sort().map((id) => {
      const definition = externalIndicatorDefinition(id)!;
      return { id, metric: definition.metric, scope: definition.scope, outputs: externalIndicatorOutputKeys(id) };
    });
    if (process.env.UPDATE_EXTERNAL_CONTRACT) writeFileSync(FIXTURE, `${JSON.stringify({ note: 'Written by externalIndicatorContract.test.ts (UPDATE_EXTERNAL_CONTRACT=1); checked by test_trading_external_indicators.py.', entries }, null, 1)}\n`);
    expect(entries).toEqual(JSON.parse(readFileSync(FIXTURE, 'utf-8')).entries);
    expect(entries.every((entry) => entry.outputs.length > 0)).toBe(true);
  });
});
