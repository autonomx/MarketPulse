import { useEffect, useRef, useState } from 'react';
import { requestPaperTicket } from './paperTicketRequests';
import { tradingApi } from './tradingApi';
import './ChartTradeButtons.css';

/** The quote is fetched again at most this often, when the chart's last price moves. */
const QUOTE_REFRESH_MS = 5_000;

const positive = (value: unknown): number | null => {
  const parsed = Number(value);
  return value !== null && value !== undefined && value !== '' && Number.isFinite(parsed) && parsed > 0 ? parsed : null;
};

function priceText(value: number | null): string {
  if (value === null) return '—';
  const digits = Math.abs(value) >= 1_000 ? 2 : Math.abs(value) >= 1 ? 4 : 6;
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

/** Bid and ask from the quote, or the last price for both when the feed has no book (or in replay). */
function useChartQuote(instrumentId: string, bindingId: string | null, lastPrice: number | null, replayMode: boolean) {
  const [quote, setQuote] = useState<{ bid: number | null; ask: number | null } | null>(null);
  const fetchedAt = useRef(0);
  useEffect(() => {
    setQuote(null);
    fetchedAt.current = 0;
  }, [instrumentId, bindingId, replayMode]);
  useEffect(() => {
    if (replayMode || Date.now() - fetchedAt.current < QUOTE_REFRESH_MS) return;
    fetchedAt.current = Date.now();
    let cancelled = false;
    void tradingApi.quote(instrumentId, bindingId).then((next) => {
      if (!cancelled) setQuote({ bid: positive(next.bid), ask: positive(next.ask) });
    }).catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [bindingId, instrumentId, lastPrice, replayMode]);
  return { bid: quote?.bid ?? lastPrice, ask: quote?.ask ?? lastPrice };
}

/**
 * Sell and buy buttons with the bid and ask in the chart legend (TVP-7.3). They fill the paper ticket with a market
 * order, which is checked and placed there like any other.
 */
export function ChartTradeButtons({
  instrumentId, bindingId, lastPrice, replayMode,
}: {
  instrumentId: string;
  bindingId: string | null;
  lastPrice: number | null;
  replayMode: boolean;
}) {
  const { bid, ask } = useChartQuote(instrumentId, bindingId, lastPrice, replayMode);
  const spread = bid !== null && ask !== null && ask > bid ? ask - bid : null;
  const order = (side: 'buy' | 'sell') => requestPaperTicket({
    instrumentId, side, orderType: 'market', entry: null, stop: null, target: null, quantity: null, source: 'chart',
  });
  return (
    <div className="chart-trade-buttons" role="group" aria-label="Paper trade" onPointerDown={(event) => event.stopPropagation()}>
      <button type="button" className="is-sell" onClick={() => order('sell')} aria-label={`Sell at ${priceText(bid)}`} title="Sell at market (opens the paper ticket)">
        <b>{priceText(bid)}</b><span>SELL</span>
      </button>
      <span className="chart-trade-spread" aria-label="Spread">{spread === null ? '' : priceText(spread)}</span>
      <button type="button" className="is-buy" onClick={() => order('buy')} aria-label={`Buy at ${priceText(ask)}`} title="Buy at market (opens the paper ticket)">
        <b>{priceText(ask)}</b><span>BUY</span>
      </button>
    </div>
  );
}
