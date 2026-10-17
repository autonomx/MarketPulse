import { describe, expect, it } from 'vitest';
import { blackScholes, pnlCurve, strategyPnl, strategySummary, type OptionLeg } from './optionsMath';

const market = { rate: 0.05, dividendYield: 0 };
const leg = (patch: Partial<OptionLeg>): OptionLeg => ({ id: 'x', symbol: 'X', kind: 'call', strike: 100, years: 30 / 365, side: 1, quantity: 1, price: 2, iv: 0.25, ...patch });

describe('options maths (TVP-10.4)', () => {
  it('prices as the server does (Hull: call 4.76, put 0.81)', () => {
    const r = { rate: 0.1, dividendYield: 0 };
    expect(blackScholes('call', 42, 40, 0.5, r, 0.2).price).toBeCloseTo(4.76, 2);
    expect(blackScholes('put', 42, 40, 0.5, r, 0.2).price).toBeCloseTo(0.81, 2);
    expect(blackScholes('call', 42, 40, 0.5, r, 0.2).delta).toBeCloseTo(0.7791, 3);
    expect(blackScholes('put', 38, 40, 0, r, 0.2)).toEqual({ price: 2, delta: -1, gamma: 0, theta: 0, vega: 0 });
  });

  it('a long call loses its premium below the strike and gains above the breakeven', () => {
    const call = [leg({ price: 2 })];
    expect(strategyPnl(call, 90, market, 'expiry')).toBeCloseTo(-200);
    expect(strategyPnl(call, 110, market, 'expiry')).toBeCloseTo(800);
    const summary = strategySummary(call, 100, market);
    expect(summary.netCost).toBe(200);
    expect(summary.breakevens).toHaveLength(1);
    expect(summary.breakevens[0]).toBeCloseTo(102, 1);
    expect(summary.maxLoss).toBeCloseTo(-200);
    expect(summary.profitUnbounded).toBe(true);
    expect(summary.lossUnbounded).toBe(false);
    expect(summary.greeks.delta).toBeGreaterThan(40);
    // Before expiry the call still holds time value.
    expect(strategyPnl(call, 100, market, { daysForward: 0, ivShift: 0 })).toBeGreaterThan(strategyPnl(call, 100, market, 'expiry'));
  });

  it('a bull call spread caps both sides; a short call is unbounded on losses', () => {
    const spread = [leg({ strike: 100, price: 3 }), leg({ strike: 110, side: -1, price: 1 })];
    const summary = strategySummary(spread, 105, market);
    expect(summary.netCost).toBe(200);
    expect(summary.maxProfit).toBeCloseTo(800);
    expect(summary.maxLoss).toBeCloseTo(-200);
    expect(summary.profitUnbounded || summary.lossUnbounded).toBe(false);
    expect(strategySummary([leg({ side: -1 })], 100, market).lossUnbounded).toBe(true);
  });

  it('values a calendar at the first expiry with the later leg still priced, and shifts volatility', () => {
    const calendar = [leg({ side: -1, years: 10 / 365, price: 1.5 }), leg({ years: 40 / 365, price: 3 })];
    const atFirst = strategyPnl(calendar, 100, market, 'expiry');
    expect(atFirst).toBeGreaterThan(0); // the long leg keeps its time value at the strike
    const higher = strategyPnl(calendar, 100, market, { daysForward: 10, ivShift: 0.5 });
    expect(higher).toBeGreaterThan(atFirst);
    const curve = pnlCurve(calendar, 100, market, 'expiry', 0.1, 4);
    expect(curve.map((point) => Math.round(point.price))).toEqual([90, 95, 100, 105, 110]);
  });
});
