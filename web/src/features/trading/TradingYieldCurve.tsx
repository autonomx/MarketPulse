/**
 * The US Treasury yield curve (TVP-10.5): a day's curve beside the curves a week, a month and a year earlier, the
 * 10Y-2Y and 10Y-3M spreads, and each maturity's FRED series to chart.
 */
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { unwrapLabelled } from '../../api/http';
import type { components } from './api/generated';
import { api } from './api/gateway';
import './TradingYieldCurve.css';

type YieldCurve = components['schemas']['YieldCurve'];

/** The FRED series of each Treasury maturity, to chart its history. */
export const TENOR_SERIES: Record<string, string> = {
  '1 Mo': 'DGS1MO', '3 Mo': 'DGS3MO', '6 Mo': 'DGS6MO', '1 Yr': 'DGS1', '2 Yr': 'DGS2', '3 Yr': 'DGS3',
  '5 Yr': 'DGS5', '7 Yr': 'DGS7', '10 Yr': 'DGS10', '20 Yr': 'DGS20', '30 Yr': 'DGS30',
};
// Palette tokens (WP-9.8), one per series in order.
const COLORS = ['var(--c-blue-540)', 'var(--c-amber-770)', 'var(--c-purple-500)', 'var(--c-slate-580)'];
const WIDTH = 640;
const HEIGHT = 220;
const PAD = { left: 40, right: 12, top: 12, bottom: 28 };

const percent = (value: number | null | undefined) => (value === null || value === undefined ? '—' : `${value.toFixed(2)}%`);

function CurveChart({ curve }: { curve: YieldCurve }) {
  const values = curve.curves.flatMap((line) => line.yields.filter((value): value is number => value !== null));
  if (values.length === 0) return null;
  const low = Math.floor(Math.min(...values) * 2) / 2;
  const high = Math.ceil(Math.max(...values) * 2) / 2 || low + 0.5;
  const x = (index: number) => PAD.left + (index / Math.max(1, curve.tenors.length - 1)) * (WIDTH - PAD.left - PAD.right);
  const y = (value: number) => PAD.top + (1 - (value - low) / Math.max(0.01, high - low)) * (HEIGHT - PAD.top - PAD.bottom);
  const ticks = Array.from({ length: Math.round((high - low) / 0.5) + 1 }, (_, index) => low + index * 0.5);
  return (
    <svg className="trading-yield-curve-chart" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`Yield curve on ${curve.curves[0].date}`}>
      {ticks.map((tick) => (
        <g key={tick}>
          <line x1={PAD.left} x2={WIDTH - PAD.right} y1={y(tick)} y2={y(tick)} className="grid" />
          <text x={PAD.left - 4} y={y(tick) + 3} textAnchor="end">{tick.toFixed(1)}</text>
        </g>
      ))}
      {curve.tenors.map((tenor, index) => <text key={tenor} x={x(index)} y={HEIGHT - 10} textAnchor="middle">{tenor.replace(' Month', 'M').replace(' Mo', 'M').replace(' Yr', 'Y')}</text>)}
      {[...curve.curves].reverse().map((line) => {
        const color = COLORS[curve.curves.indexOf(line) % COLORS.length];
        const points = line.yields.flatMap((value, index) => (value === null ? [] : [`${x(index)},${y(value)}`]));
        return (
          <g key={line.label}>
            <polyline points={points.join(' ')} fill="none" style={{ stroke: color }} strokeWidth={line === curve.curves[0] ? 2.5 : 1.5} strokeDasharray={line === curve.curves[0] ? undefined : '4 3'} />
            {line === curve.curves[0] ? line.yields.map((value, index) => (value === null ? null : <circle key={index} cx={x(index)} cy={y(value)} r={3} style={{ fill: color }} />)) : null}
          </g>
        );
      })}
    </svg>
  );
}

export function TradingYieldCurve({ onShowInstrument }: { onShowInstrument?: (instrumentId: string) => void }) {
  const [on, setOn] = useState('');
  const query = useQuery({
    queryKey: ['trading', 'macro', 'yield-curve', on],
    queryFn: () => unwrapLabelled(api.GET('/api/trading/macro/yield-curve', { params: { query: on ? { on } : {} } }), 'Yield curve') as Promise<YieldCurve>,
    staleTime: 30 * 60_000,
  });
  const curve = query.data;
  return (
    <section className="trading-yield-curve" aria-label="US Treasury yield curve">
      <header>
        <label>Date <input type="date" value={on} max={new Date().toISOString().slice(0, 10)} onChange={(event) => setOn(event.target.value)} /></label>
        {on ? <button type="button" onClick={() => setOn('')}>Latest</button> : null}
        {curve ? <span>10Y–2Y <strong>{percent(curve.spreads['10Y-2Y'])}</strong> · 10Y–3M <strong>{percent(curve.spreads['10Y-3M'])}</strong></span> : null}
      </header>
      {query.isLoading ? <p role="status">Loading the yield curve…</p> : null}
      {query.isError ? <p role="alert">{query.error instanceof Error ? query.error.message : 'The yield curve could not load.'}</p> : null}
      {curve ? (
        <>
          <ul className="trading-yield-curve-legend" aria-label="Curves">
            {curve.curves.map((line, index) => <li key={line.label}><i style={{ background: COLORS[index % COLORS.length] }} />{line.label} ({line.date})</li>)}
          </ul>
          <CurveChart curve={curve} />
          <table aria-label="Yields by maturity">
            <thead><tr><th>Maturity</th>{curve.curves.map((line) => <th key={line.label}>{line.label}</th>)}</tr></thead>
            <tbody>
              {curve.tenors.map((tenor, index) => (
                <tr key={tenor}>
                  <td>
                    {TENOR_SERIES[tenor] && onShowInstrument
                      ? <button type="button" onClick={() => onShowInstrument(`economic:FRED:${TENOR_SERIES[tenor]}`)} aria-label={`Chart the ${tenor} yield`}>{tenor}</button>
                      : tenor}
                  </td>
                  {curve.curves.map((line) => <td key={line.label}>{percent(line.yields[index])}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
          <footer>{curve.source}. A maturity's history charts from FRED (needs a FRED key).</footer>
        </>
      ) : null}
    </section>
  );
}
