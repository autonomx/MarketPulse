import { watchlistLogoIdentity } from './tradingWatchlistPresentation';

export function TradingWatchlistLogo({ symbol, instrumentId }: { symbol: string; instrumentId: string }) {
  const identity = watchlistLogoIdentity(symbol, instrumentId);
  return (
    <span
      className={`trading-watchlist-asset-icon ${identity.kind}`}
      role="img"
      aria-label={`${identity.label} logo`}
      title={identity.label}
    >
      {identity.kind === 'apple' ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M15.9 5.1c.7-.9 1.2-2.1 1.1-3.3-1.1.1-2.4.8-3.2 1.7-.7.8-1.3 2-1.1 3.1 1.2.1 2.4-.6 3.2-1.5ZM19.9 13.1c0-2.5 2.1-3.7 2.2-3.8-1.2-1.7-3.1-1.9-3.8-1.9-1.6-.2-3.1 1-3.9 1-.8 0-2-.9-3.3-.9-1.7 0-3.3 1-4.2 2.5-1.8 3-.5 7.4 1.3 9.8.9 1.2 1.9 2.5 3.2 2.4 1.3-.1 1.8-.8 3.4-.8s2 .8 3.3.8c1.4 0 2.2-1.2 3.1-2.5 1-1.4 1.4-2.8 1.4-2.9-.1 0-2.7-1-2.7-3.7Z" />
        </svg>
      ) : identity.kind === 'nvidia' ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M3 12c2.5-4 5.5-6 9-6s6.5 2 9 6c-2.5 4-5.5 6-9 6s-6.5-2-9-6Z" />
          <path d="M8 16V8l8 8V8" />
        </svg>
      ) : identity.kind === 'tesla' ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 6c2.4-1.3 5.1-1.8 8-1.8S17.6 4.7 20 6M6 7h12M12 7v12" />
        </svg>
      ) : identity.kind === 'bitcoin' ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M9 5v14M13 5v14M7 8h6.1c2.1 0 3.4 1 3.4 2.5S15.2 13 13.1 13H7m0 0h6.8c2.2 0 3.5 1.1 3.5 2.7S15.8 18 13.5 18H7" />
        </svg>
      ) : identity.kind === 'ethereum' ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="m12 2-5 10 5 3 5-3-5-10Z" />
          <path d="m7 13 5 9 5-9-5 3-5-3Z" />
        </svg>
      ) : identity.kind === 'solana' ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M5 7h13l-2 3H3l2-3ZM8 11h13l-2 3H6l2-3ZM5 15h13l-2 3H3l2-3Z" />
        </svg>
      ) : identity.kind === 'hyperliquid' ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M5 5v14M19 5v14M5 12h14M5 5h5v14h4V5h5" />
        </svg>
      ) : (
        identity.mark
      )}
    </span>
  );
}
