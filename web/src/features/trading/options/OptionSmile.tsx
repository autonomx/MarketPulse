/** The volatility smile (TVP-10.4): implied volatility by strike for calls and puts of one expiration. */
import type { components } from '../api/generated';

type Chain = components['schemas']['OptionChain'];

const WIDTH = 560;
const HEIGHT = 200;
const PAD = { left: 40, right: 10, top: 10, bottom: 24 };

export function OptionSmile({ chain }: { chain: Chain }) {
  const calls = chain.rows.flatMap((row) => (row.call?.iv ? [{ strike: row.strike, iv: row.call.iv }] : []));
  const puts = chain.rows.flatMap((row) => (row.put?.iv ? [{ strike: row.strike, iv: row.put.iv }] : []));
  const all = [...calls, ...puts];
  if (all.length < 2) return <p>Not enough priced contracts for a volatility curve.</p>;
  const strikes = all.map((point) => point.strike);
  const ivs = all.map((point) => point.iv);
  const [minStrike, maxStrike] = [Math.min(...strikes), Math.max(...strikes)];
  const [minIv, maxIv] = [Math.min(...ivs), Math.max(...ivs)];
  const x = (strike: number) => PAD.left + ((strike - minStrike) / Math.max(1e-9, maxStrike - minStrike)) * (WIDTH - PAD.left - PAD.right);
  const y = (iv: number) => PAD.top + (1 - (iv - minIv) / Math.max(1e-9, maxIv - minIv)) * (HEIGHT - PAD.top - PAD.bottom);
  const line = (points: typeof calls) => points.map((point) => `${x(point.strike)},${y(point.iv)}`).join(' ');
  const spot = chain.underlying_price;
  return (
    <figure className="trading-option-smile">
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`Implied volatility by strike, ${chain.expiration}`}>
        {[minIv, (minIv + maxIv) / 2, maxIv].map((iv) => <text key={iv} x={PAD.left - 4} y={y(iv) + 3} textAnchor="end">{(iv * 100).toFixed(0)}%</text>)}
        {[minStrike, (minStrike + maxStrike) / 2, maxStrike].map((strike) => <text key={strike} x={x(strike)} y={HEIGHT - 6} textAnchor="middle">{strike.toFixed(0)}</text>)}
        {spot !== null && spot !== undefined && spot >= minStrike && spot <= maxStrike ? <line x1={x(spot)} x2={x(spot)} y1={PAD.top} y2={HEIGHT - PAD.bottom} className="spot" /> : null}
        <polyline points={line(calls)} className="calls" fill="none" />
        <polyline points={line(puts)} className="puts" fill="none" />
      </svg>
      <figcaption><span className="calls">Calls</span> <span className="puts">Puts</span> · the dashed line is the underlying price</figcaption>
    </figure>
  );
}
