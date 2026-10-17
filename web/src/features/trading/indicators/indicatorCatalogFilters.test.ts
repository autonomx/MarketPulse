import { describe, expect, it } from 'vitest';
import {
  classifyIndicatorCatalogEntry,
  indicatorAvailabilityMatches,
  indicatorMarketMatches,
} from './indicatorCatalogFilters';

describe('indicator catalog filters', () => {
  it('classifies universal technical studies by category', () => {
    expect(classifyIndicatorCatalogEntry('Relative Strength Index (RSI)', 'indicator')).toEqual({
      markets: ['universal'],
      category: 'momentum',
    });
    expect(classifyIndicatorCatalogEntry('Volume Profile', 'profile')).toEqual({
      markets: ['universal'],
      category: 'volume',
    });
  });

  it('separates stock, crypto, and derivatives-specific studies', () => {
    expect(classifyIndicatorCatalogEntry('Analyst price forecast', 'indicator')).toEqual({
      markets: ['stocks'],
      category: 'fundamentals',
    });
    expect(classifyIndicatorCatalogEntry('Active addresses with contracts', 'indicator')).toEqual({
      markets: ['crypto'],
      category: 'on-chain',
    });
    expect(classifyIndicatorCatalogEntry('Difficulty', 'indicator')).toEqual({
      markets: ['crypto'],
      category: 'on-chain',
    });
    expect(classifyIndicatorCatalogEntry('Mean block size in bytes', 'indicator')).toEqual({
      markets: ['crypto'],
      category: 'on-chain',
    });
    expect(classifyIndicatorCatalogEntry('Open Interest', 'indicator')).toEqual({
      markets: ['derivatives'],
      category: 'derivatives',
    });
    expect(classifyIndicatorCatalogEntry('Understanding crypto open interest', 'indicator')).toEqual({
      markets: ['crypto', 'derivatives'],
      category: 'derivatives',
    });
    // Calculated from bars, so a volume study rather than on-chain data; still a crypto (24/7) market study.
    expect(classifyIndicatorCatalogEntry('24-hour Volume', 'indicator')).toEqual({
      markets: ['crypto'],
      category: 'volume',
    });
    expect(classifyIndicatorCatalogEntry('Mark price', 'indicator')).toEqual({
      markets: ['derivatives'],
      category: 'derivatives',
    });
  });

  it('keeps universal indicators visible inside a selected market', () => {
    expect(indicatorMarketMatches(['universal'], 'stocks')).toBe(true);
    expect(indicatorMarketMatches(['universal'], 'crypto')).toBe(true);
    expect(indicatorMarketMatches(['crypto'], 'crypto')).toBe(true);
    expect(indicatorMarketMatches(['crypto'], 'stocks')).toBe(false);
    expect(indicatorMarketMatches(['stocks'], 'universal')).toBe(false);
  });

  it('filters availability without conflating missing data with unsupported markets', () => {
    expect(indicatorAvailabilityMatches(true, 'ready')).toBe(true);
    expect(indicatorAvailabilityMatches(false, 'ready')).toBe(false);
    expect(indicatorAvailabilityMatches(false, 'data-required')).toBe(true);
    expect(indicatorAvailabilityMatches(true, 'all')).toBe(true);
  });
});
