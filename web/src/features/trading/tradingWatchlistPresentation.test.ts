import { describe, expect, it } from 'vitest';
import {
  formatWatchlistPrice,
  watchlistDisplaySymbol,
  watchlistLogoIdentity,
} from './tradingWatchlistPresentation';

describe('trading watchlist presentation', () => {
  it('formats prices with exactly two decimal places', () => {
    expect(formatWatchlistPrice('305.26000977')).toBe('305.26');
    expect(formatWatchlistPrice('63336.26')).toBe('63,336.26');
    expect(formatWatchlistPrice('75')).toBe('75.00');
    expect(formatWatchlistPrice(null)).toBe('—');
  });

  it('uses a useful symbol when an old instrument is not in the catalog', () => {
    expect(watchlistDisplaySymbol(undefined, 'crypto:HYPERLIQUID:perpetual:BTC-USD')).toBe('BTCUSD');
    expect(watchlistDisplaySymbol('AAPL', 'equity:YAHOO:AAPL')).toBe('AAPL');
  });

  it('selects symbol-specific logo identities', () => {
    expect(watchlistLogoIdentity('AAPL', 'equity:YAHOO:AAPL').kind).toBe('apple');
    expect(watchlistLogoIdentity('BTCUSDT', 'crypto:BINANCE:spot:BTC-USDT').kind).toBe('bitcoin');
    expect(watchlistLogoIdentity('BTCUSD', 'crypto:HYPERLIQUID:perpetual:BTC-USD').kind).toBe('bitcoin');
    expect(watchlistLogoIdentity('HYPE', 'crypto:HYPERLIQUID:perpetual:HYPE-USD').kind).toBe('hyperliquid');
  });
});
