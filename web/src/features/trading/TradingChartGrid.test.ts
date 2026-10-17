import { describe, expect, it } from 'vitest';
import { tradingGridColumns } from './TradingChartGrid';

describe('Trading flexible chart grid', () => {
  it('automatically reflows one through sixteen charts', () => {
    expect(tradingGridColumns('auto', 1)).toBe(1);
    expect(tradingGridColumns('auto', 2)).toBe(2);
    expect(tradingGridColumns('auto', 3)).toBe(2);
    expect(tradingGridColumns('auto', 4)).toBe(2);
    expect(tradingGridColumns('auto', 5)).toBe(3);
    expect(tradingGridColumns('auto', 9)).toBe(3);
    expect(tradingGridColumns('auto', 10)).toBe(4);
    expect(tradingGridColumns('auto', 16)).toBe(4);
  });

  it('honors an explicit user-selected column count', () => {
    expect(tradingGridColumns('columns-1', 8)).toBe(1);
    expect(tradingGridColumns('columns-2', 3)).toBe(2);
    expect(tradingGridColumns('columns-3', 7)).toBe(3);
    expect(tradingGridColumns('columns-4', 2)).toBe(4);
  });

  it('maps TradingView-style row and split layouts to their grid columns', () => {
    expect(tradingGridColumns('rows-2', 2)).toBe(1);
    expect(tradingGridColumns('rows-4', 8)).toBe(1);
    expect(tradingGridColumns('main-left-3', 3)).toBe(2);
    expect(tradingGridColumns('main-bottom-3', 3)).toBe(2);
  });
});
