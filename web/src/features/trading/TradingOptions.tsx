/**
 * Options (TVP-10.4, D-6): a US stock's option chain by expiration from Alpaca's option data, its volatility smile,
 * and a strategy builder. Research only: no options orders.
 */
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { unwrapLabelled } from '../../api/http';
import type { components } from './api/generated';
import { api } from './api/gateway';
import { isStockInstrument } from './corporateEvents';
import { OptionChainTable, type LegPick } from './options/OptionChainTable';
import { OptionSmile } from './options/OptionSmile';
import type { OptionLeg } from './options/optionsMath';
import { StrategyBuilder } from './options/StrategyBuilder';
import './TradingOptions.css';

type Chain = components['schemas']['OptionChain'];
type Expiration = components['schemas']['OptionExpiration'];

function legFrom(pick: LegPick, chain: Chain): OptionLeg | null {
  const price = (pick.side === 1 ? pick.quote.ask : pick.quote.bid) ?? pick.quote.mark;
  if (price === null || price === undefined || !pick.quote.iv) return null;
  return {
    id: crypto.randomUUID(), symbol: pick.quote.symbol, kind: pick.kind, strike: pick.strike, years: chain.years_to_expiry,
    side: pick.side, quantity: 1, price, iv: pick.quote.iv,
  };
}

export function TradingOptions({ instrumentId }: { instrumentId: string }) {
  const stock = isStockInstrument(instrumentId);
  const [chosen, setChosen] = useState('');
  const [view, setView] = useState<'chain' | 'smile'>('chain');
  const [legs, setLegs] = useState<OptionLeg[]>([]);
  const [notice, setNotice] = useState('');
  const expirations = useQuery({
    queryKey: ['trading', 'options', 'expirations', instrumentId],
    queryFn: () => unwrapLabelled(api.GET('/api/trading/options/expirations', { params: { query: { instrument_id: instrumentId } } }), 'Option expirations') as Promise<Expiration[]>,
    enabled: stock,
    staleTime: 30 * 60_000,
  });
  const expiration = chosen || expirations.data?.[0]?.date || '';
  const chain = useQuery({
    queryKey: ['trading', 'options', 'chain', instrumentId, expiration],
    queryFn: () => unwrapLabelled(api.GET('/api/trading/options/chain', { params: { query: { instrument_id: instrumentId, expiration } } }), 'Option chain') as Promise<Chain>,
    enabled: stock && Boolean(expiration),
    staleTime: 60_000,
  });
  if (!stock) return <p className="trading-options">Option chains are for US stocks and ETFs.</p>;
  const data = chain.data;
  const spot = data?.underlying_price ?? null;
  const pick = (choice: LegPick) => {
    if (!data) return;
    const leg = legFrom(choice, data);
    if (leg) setLegs((current) => [...current, leg]);
    setNotice(leg ? '' : 'That contract has no price or volatility to model.');
  };
  return (
    <section className="trading-options" aria-label="Options">
      <header>
        <select aria-label="Expiration" value={expiration} onChange={(event) => setChosen(event.target.value)}>
          {(expirations.data ?? []).map((item) => <option key={item.date} value={item.date}>{item.date} ({item.days}d)</option>)}
        </select>
        <div role="tablist" aria-label="Option views">
          <button type="button" role="tab" aria-selected={view === 'chain'} onClick={() => setView('chain')}>Chain</button>
          <button type="button" role="tab" aria-selected={view === 'smile'} onClick={() => setView('smile')}>Volatility</button>
        </div>
        {data ? <span>Underlying {spot?.toFixed(2) ?? '—'} · rate {(data.rate * 100).toFixed(2)}% · dividend yield {(data.dividend_yield * 100).toFixed(2)}%</span> : null}
      </header>
      {expirations.isLoading || chain.isLoading ? <p role="status">Loading option data…</p> : null}
      {expirations.isError || chain.isError ? <p role="alert">{((expirations.error ?? chain.error) as Error | null)?.message ?? 'Option data could not load.'}</p> : null}
      {notice ? <p role="status">{notice}</p> : null}
      <div className="trading-options-body">
        <div className="trading-options-chain">
          {data && view === 'chain' ? <OptionChainTable chain={data} onPick={pick} /> : null}
          {data && view === 'smile' ? <OptionSmile chain={data} /> : null}
        </div>
        {spot !== null ? (
          <StrategyBuilder
            legs={legs} spot={spot} market={{ rate: data?.rate ?? 0.04, dividendYield: data?.dividend_yield ?? 0 }}
            onChangeLeg={(id, patch) => setLegs((current) => current.map((leg) => (leg.id === id ? { ...leg, ...patch } : leg)))}
            onRemoveLeg={(id) => setLegs((current) => current.filter((leg) => leg.id !== id))}
            onClear={() => setLegs([])}
          />
        ) : null}
      </div>
      <footer>{data?.source ?? 'Alpaca options market data'}. Implied volatility and Greeks from the quote mid (Black-Scholes). Research only.</footer>
    </section>
  );
}
