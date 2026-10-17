/**
 * The seasonals panel (TVP-10.3): the active symbol's yearly curves on one January–December axis (the current year
 * highlighted, the average of past years dashed) and a table of monthly returns, from its daily bars.
 */
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { averageCurve, isDailyHistory, monthlyReturns, monthlySummary, yearlyCurves } from './seasonals';
import { tradingApi } from './tradingApi';
import './TradingSeasonals.css';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTH_STARTS = [0, 31, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335];
// Palette tokens (WP-9.8), one per series in order.
const COLORS = ['var(--c-blue-540)', 'var(--c-amber-770)', 'var(--c-teal-600)', 'var(--c-purple-500)', 'var(--c-pink-660)', 'var(--c-cyan-720)', 'var(--c-amber-480)', 'var(--c-blue-560)', 'var(--c-green-790)', 'var(--c-amber-700)'];
const percent = (value: number | null) => (value === null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(1)}%`);

export function TradingSeasonals({ instrumentId, bindingId }: { instrumentId: string; bindingId: string | null }) {
  const [years, setYears] = useState(5);
  const [showAverage, setShowAverage] = useState(true);
  const history = useQuery({
    queryKey: ['trading', 'seasonals', instrumentId, bindingId],
    queryFn: () => tradingApi.bars(instrumentId, '1d', 5_000, bindingId),
    staleTime: 60 * 60_000,
  });
  const bars = useMemo(() => history.data?.bars ?? [], [history.data]);
  const curves = useMemo(() => yearlyCurves(bars, years), [bars, years]);
  const average = useMemo(() => averageCurve(curves.filter((curve) => curve.year !== curves[0]?.year)), [curves]);
  const table = useMemo(() => monthlyReturns(bars, years), [bars, years]);
  const summary = useMemo(() => monthlySummary(table), [table]);

  if (history.isLoading) return <p className="trading-seasonals" role="status">Loading daily history…</p>;
  if (history.isError) return <p className="trading-seasonals" role="alert">The daily history could not load.</p>;
  if (!isDailyHistory(bars) || curves.length === 0) return <p className="trading-seasonals">Not enough daily history for seasonals.</p>;
  const values = [...curves.flatMap((curve) => curve.points.map((point) => point.change)), ...(showAverage ? average.map((point) => point.change) : [])];
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = max - min || 1;
  const x = (day: number) => (day / 365) * 1000;
  const y = (change: number) => 300 - ((change - min) / span) * 300;
  const path = (points: ReadonlyArray<{ day: number; change: number }>) => points.map((point, index) => `${index ? 'L' : 'M'}${x(point.day).toFixed(1)},${y(point.change).toFixed(1)}`).join(' ');
  return (
    <section className="trading-seasonals" aria-label="Seasonals">
      <header>
        <label>Years<select aria-label="Seasonal years" value={years} onChange={(event) => setYears(Number(event.target.value))}>{[3, 5, 10, 15].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        <label><input type="checkbox" checked={showAverage} onChange={(event) => setShowAverage(event.target.checked)} />Average of past years</label>
      </header>
      <svg viewBox="0 0 1000 300" preserveAspectRatio="none" role="img" aria-label={`Yearly performance, ${curves.length} years`} className="trading-seasonals-chart">
        {MONTH_STARTS.map((start) => <line key={start} x1={x(start)} x2={x(start)} y1={0} y2={300} className="is-grid" />)}
        <line x1={0} x2={1000} y1={y(0)} y2={y(0)} className="is-zero" />
        {showAverage && average.length ? <path d={path(average)} className="is-average" /> : null}
        {[...curves].reverse().map((curve) => (
          <path key={curve.year} d={path(curve.points)} style={{ stroke: COLORS[curves.indexOf(curve) % COLORS.length] }} className={curve === curves[0] ? 'is-current' : undefined} />
        ))}
      </svg>
      <div className="trading-seasonals-axis" aria-hidden="true">{MONTHS.map((month) => <span key={month}>{month}</span>)}</div>
      <ul className="trading-seasonals-legend" aria-label="Years">
        {curves.map((curve, index) => (
          <li key={curve.year}><i style={{ background: COLORS[index % COLORS.length] }} />{curve.year}: {percent(curve.points.at(-1)?.change ?? null)}{curve.complete ? '' : ' so far'}</li>
        ))}
      </ul>
      <div className="trading-seasonals-table">
        <table aria-label="Monthly returns">
          <thead><tr><th>Year</th>{MONTHS.map((month) => <th key={month}>{month}</th>)}<th>Year</th></tr></thead>
          <tbody>
            {table.map((row) => (
              <tr key={row.year}>
                <th scope="row">{row.year}</th>
                {row.months.map((value, month) => <td key={month} className={value === null ? '' : value >= 0 ? 'is-up' : 'is-down'}>{percent(value)}</td>)}
                <td className={row.total === null ? '' : row.total >= 0 ? 'is-up' : 'is-down'}>{percent(row.total)}</td>
              </tr>
            ))}
            <tr className="is-summary">
              <th scope="row">Average</th>
              {summary.map((item, month) => <td key={month} title={item.up === null ? undefined : `Rose in ${Math.round(item.up * 100)}% of years`}>{percent(item.average)}</td>)}
              <td />
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}
