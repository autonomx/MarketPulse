export type WatchlistLogoKind =
  | 'apple'
  | 'bitcoin'
  | 'ethereum'
  | 'hyperliquid'
  | 'nvidia'
  | 'solana'
  | 'spy'
  | 'tesla'
  | 'generic';

export type WatchlistLogoIdentity = {
  kind: WatchlistLogoKind;
  label: string;
  mark: string;
};

const PRICE_FORMATTER = new Intl.NumberFormat('en-US', {
  maximumFractionDigits: 2,
  minimumFractionDigits: 2,
});

export function formatWatchlistPrice(value: string | null | undefined): string {
  if (!value) return '—';
  const numeric = Number(value);
  return Number.isFinite(numeric) ? PRICE_FORMATTER.format(numeric) : value;
}

export function watchlistDisplaySymbol(symbol: string | undefined, instrumentId: string): string {
  if (symbol) return symbol;
  const lastSegment = instrumentId.split(':').at(-1);
  return lastSegment?.replace(/[-_/]/g, '') || instrumentId;
}

export function watchlistLogoIdentity(symbol: string, instrumentId: string): WatchlistLogoIdentity {
  const key = `${symbol} ${instrumentId}`.toUpperCase();
  if (key.includes('AAPL')) return { kind: 'apple', label: 'Apple', mark: 'A' };
  if (key.includes('NVDA')) return { kind: 'nvidia', label: 'NVIDIA', mark: 'N' };
  if (key.includes('TSLA')) return { kind: 'tesla', label: 'Tesla', mark: 'T' };
  if (key.includes('SPY')) return { kind: 'spy', label: 'SPDR S&P 500 ETF', mark: 'S' };
  if (key.includes('BTC')) return { kind: 'bitcoin', label: 'Bitcoin', mark: 'B' };
  if (key.includes('ETH')) return { kind: 'ethereum', label: 'Ethereum', mark: '◆' };
  if (key.includes('SOL')) return { kind: 'solana', label: 'Solana', mark: '≋' };
  if (key.includes('HYPERLIQ') || key.includes('HYPE')) {
    return { kind: 'hyperliquid', label: 'Hyperliquid', mark: 'H' };
  }
  return { kind: 'generic', label: symbol, mark: symbol.slice(0, 1).toUpperCase() || '?' };
}
