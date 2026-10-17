/**
 * The options strategy builder (TVP-10.4): legs picked from the chain, their P&L across underlying prices at expiry
 * and at a chosen date (what-if: days forward, volatility shift), breakevens, max profit and loss, and net Greeks.
 * Research only: nothing here places an order.
 */
import { useMemo, useState } from 'react';
import { CONTRACT_SIZE, pnlCurve, strategySummary, type Market, type OptionLeg } from './optionsMath';

const WIDTH = 560;
const HEIGHT = 220;
const PAD = { left: 56, right: 10, top: 10, bottom: 24 };

const money = (value: number) => `${value < 0 ? '−' : ''}$${Math.abs(value).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

function PnlChart({ legs, spot, market, daysForward, ivShift }: { legs: OptionLeg[]; spot: number; market: Market; daysForward: number; ivShift: number }) {
  const expiry = pnlCurve(legs, spot, market, 'expiry');
  const dated = pnlCurve(legs, spot, market, { daysForward, ivShift });
  const values = [...expiry, ...dated].map((point) => point.pnl);
  const [low, high] = [Math.min(0, ...values), Math.max(0, ...values)];
  const prices = expiry.map((point) => point.price);
  const [left, right] = [prices[0], prices[prices.length - 1]];
  const x = (price: number) => PAD.left + ((price - left) / (right - left)) * (WIDTH - PAD.left - PAD.right);
  const y = (pnl: number) => PAD.top + (1 - (pnl - low) / Math.max(1e-9, high - low)) * (HEIGHT - PAD.top - PAD.bottom);
  const line = (curve: typeof expiry) => curve.map((point) => `${x(point.price)},${y(point.pnl)}`).join(' ');
  return (
    <svg className="trading-option-pnl" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label="Profit and loss by underlying price">
      <line x1={PAD.left} x2={WIDTH - PAD.right} y1={y(0)} y2={y(0)} className="zero" />
      <line x1={x(spot)} x2={x(spot)} y1={PAD.top} y2={HEIGHT - PAD.bottom} className="spot" />
      {[low, high].map((value) => <text key={value} x={PAD.left - 4} y={y(value) + 3} textAnchor="end">{money(value)}</text>)}
      {[left, spot, right].map((price) => <text key={price} x={x(price)} y={HEIGHT - 6} textAnchor="middle">{price.toFixed(0)}</text>)}
      <polyline points={line(expiry)} className="expiry" fill="none" />
      <polyline points={line(dated)} className="dated" fill="none" />
    </svg>
  );
}

type Props = {
  legs: OptionLeg[];
  spot: number;
  market: Market;
  onChangeLeg: (id: string, patch: Partial<OptionLeg>) => void;
  onRemoveLeg: (id: string) => void;
  onClear: () => void;
};

export function StrategyBuilder({ legs, spot, market, onChangeLeg, onRemoveLeg, onClear }: Props) {
  const nearest = legs.length ? Math.min(...legs.map((leg) => leg.years)) * 365 : 0;
  const [daysForward, setDaysForward] = useState(0);
  const [ivShift, setIvShift] = useState(0);
  const days = Math.min(daysForward, Math.floor(nearest));
  const summary = useMemo(() => (legs.length ? strategySummary(legs, spot, market) : null), [legs, spot, market]);
  if (!summary) return <p className="trading-option-builder-empty">Pick a bid (sell) or an ask (buy) in the chain to start a strategy.</p>;
  const edge = (value: number, unbounded: boolean) => (unbounded ? 'Unlimited' : money(value));
  return (
    <section className="trading-option-builder" aria-label="Strategy builder">
      <table aria-label="Legs">
        <thead><tr><th>Side</th><th>Qty</th><th>Contract</th><th>Price</th><th>IV</th><th /></tr></thead>
        <tbody>
          {legs.map((leg) => (
            <tr key={leg.id}>
              <td>
                <button type="button" onClick={() => onChangeLeg(leg.id, { side: leg.side === 1 ? -1 : 1 })} aria-label={`${leg.side === 1 ? 'Buy' : 'Sell'} ${leg.symbol}: switch side`}>{leg.side === 1 ? 'Buy' : 'Sell'}</button>
              </td>
              <td><input aria-label={`Quantity of ${leg.symbol}`} type="number" min={1} step={1} value={leg.quantity} onChange={(event) => onChangeLeg(leg.id, { quantity: Math.max(1, Math.round(Number(event.target.value) || 1)) })} /></td>
              <td>{leg.strike} {leg.kind} · {Math.round(leg.years * 365)}d</td>
              <td>{leg.price.toFixed(2)}</td>
              <td>{(leg.iv * 100).toFixed(1)}%</td>
              <td><button type="button" onClick={() => onRemoveLeg(leg.id)} aria-label={`Remove ${leg.symbol}`}>×</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <dl className="trading-option-summary" aria-label="Strategy summary">
        <div><dt>{summary.netCost >= 0 ? 'Net debit' : 'Net credit'}</dt><dd>{money(Math.abs(summary.netCost))}</dd></div>
        <div><dt>Max profit</dt><dd>{edge(summary.maxProfit, summary.profitUnbounded)}</dd></div>
        <div><dt>Max loss</dt><dd>{edge(summary.maxLoss, summary.lossUnbounded)}</dd></div>
        <div><dt>Breakevens</dt><dd>{summary.breakevens.length ? summary.breakevens.map((price) => price.toFixed(2)).join(', ') : '—'}</dd></div>
        <div><dt>Delta</dt><dd>{summary.greeks.delta.toFixed(1)}</dd></div>
        <div><dt>Gamma</dt><dd>{summary.greeks.gamma.toFixed(2)}</dd></div>
        <div><dt>Theta / day</dt><dd>{money(summary.greeks.theta)}</dd></div>
        <div><dt>Vega / 1%</dt><dd>{money(summary.greeks.vega)}</dd></div>
      </dl>
      <div className="trading-option-whatif">
        <label>Days forward <input type="range" min={0} max={Math.max(0, Math.floor(nearest))} value={days} onChange={(event) => setDaysForward(Number(event.target.value))} /> {days}</label>
        <label>Volatility <input type="range" min={-50} max={50} value={Math.round(ivShift * 100)} onChange={(event) => setIvShift(Number(event.target.value) / 100)} /> {ivShift >= 0 ? '+' : ''}{Math.round(ivShift * 100)}%</label>
        <button type="button" onClick={onClear}>Clear legs</button>
      </div>
      <PnlChart legs={legs} spot={spot} market={market} daysForward={days} ivShift={ivShift} />
      <small>Solid: at the first expiry. Dashed: {days} days from now with volatility {ivShift >= 0 ? '+' : ''}{Math.round(ivShift * 100)}%. Per {CONTRACT_SIZE}-share contract; research only, no orders.</small>
    </section>
  );
}
