/** An expiration's chain (TVP-10.4): calls left, strike, puts right; a bid or ask adds a leg to the builder. */
import type { components } from '../api/generated';
import type { OptionKind } from './optionsMath';

type Chain = components['schemas']['OptionChain'];
type Quote = components['schemas']['OptionQuote'];

export type LegPick = { quote: Quote; kind: OptionKind; strike: number; side: 1 | -1 };

const money = (value: number | null | undefined) => (value === null || value === undefined ? '—' : value.toFixed(2));
const percent = (value: number | null | undefined) => (value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`);
const greek = (value: number | null | undefined, digits = 3) => (value === null || value === undefined ? '—' : value.toFixed(digits));
const count = (value: number | null | undefined) => (value === null || value === undefined ? '—' : value.toLocaleString());

function Side({ quote, kind, strike, onPick }: { quote: Quote | null | undefined; kind: OptionKind; strike: number; onPick: (pick: LegPick) => void }) {
  if (!quote) return <td colSpan={6} className="is-empty" />;
  const pick = (side: 1 | -1) => onPick({ quote, kind, strike, side });
  const cells = [
    <td key="iv">{percent(quote.iv)}</td>,
    <td key="delta">{greek(quote.delta)}</td>,
    <td key="oi">{count(quote.open_interest)}</td>,
    <td key="volume">{count(quote.volume)}</td>,
    <td key="bid"><button type="button" onClick={() => pick(-1)} aria-label={`Sell the ${strike} ${kind} at ${money(quote.bid)}`}>{money(quote.bid)}</button></td>,
    <td key="ask"><button type="button" onClick={() => pick(1)} aria-label={`Buy the ${strike} ${kind} at ${money(quote.ask)}`}>{money(quote.ask)}</button></td>,
  ];
  return <>{kind === 'call' ? cells : [...cells].reverse()}</>;
}

export function OptionChainTable({ chain, onPick }: { chain: Chain; onPick: (pick: LegPick) => void }) {
  const spot = chain.underlying_price;
  const atm = spot === null || spot === undefined ? null : chain.rows.reduce<number | null>((best, row) => (best === null || Math.abs(row.strike - spot) < Math.abs(best - spot) ? row.strike : best), null);
  return (
    <table className="trading-option-chain" aria-label={`Option chain for ${chain.expiration}`}>
      <thead>
        <tr><th colSpan={6}>Calls</th><th>Strike</th><th colSpan={6}>Puts</th></tr>
        <tr>
          <th>IV</th><th>Delta</th><th>OI</th><th>Volume</th><th>Bid</th><th>Ask</th>
          <th />
          <th>Ask</th><th>Bid</th><th>Volume</th><th>OI</th><th>Delta</th><th>IV</th>
        </tr>
      </thead>
      <tbody>
        {chain.rows.map((row) => (
          <tr key={row.strike} className={`${row.strike === atm ? 'is-atm' : ''} ${spot !== null && spot !== undefined && row.strike < spot ? 'is-call-itm' : 'is-put-itm'}`}>
            <Side quote={row.call} kind="call" strike={row.strike} onPick={onPick} />
            <th scope="row">{row.strike}</th>
            <Side quote={row.put} kind="put" strike={row.strike} onPick={onPick} />
          </tr>
        ))}
      </tbody>
    </table>
  );
}
