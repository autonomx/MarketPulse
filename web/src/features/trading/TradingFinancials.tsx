/**
 * The financials panel (TVP-10.2): the active US stock's income statement, balance sheet and cash flow (annual or
 * quarterly, from its SEC filings), with key ratios and revenue and net income by period.
 */
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { unwrapLabelled } from '../../api/http';
import type { components } from './api/generated';
import { api } from './api/gateway';
import './TradingFinancials.css';

type Financials = components['schemas']['Financials'];
type Tab = 'overview' | 'income' | 'balance' | 'cash_flow';
type Row = { end: string; values: Record<string, number> };

const ROWS: Record<Exclude<Tab, 'overview'>, string[]> = {
  income: ['revenue', 'cost_of_revenue', 'gross_profit', 'research_development', 'selling_general_admin', 'operating_income', 'pretax_income', 'income_tax', 'net_income', 'eps_basic', 'eps_diluted', 'shares_diluted'],
  balance: ['cash', 'inventory', 'current_assets', 'total_assets', 'current_liabilities', 'long_term_debt', 'total_liabilities', 'equity'],
  cash_flow: ['operating_cash_flow', 'investing_cash_flow', 'financing_cash_flow', 'capital_expenditure', 'free_cash_flow', 'dividends_paid', 'share_repurchases'],
};
const PER_SHARE = new Set(['eps_basic', 'eps_diluted']);
const RATIOS: Array<[string, string, 'money' | 'ratio' | 'percent']> = [
  ['market_cap', 'Market cap', 'money'], ['pe_ratio', 'P/E (TTM)', 'ratio'], ['ps_ratio', 'P/S (TTM)', 'ratio'], ['pb_ratio', 'P/B', 'ratio'],
  ['eps_ttm', 'EPS (TTM)', 'ratio'], ['revenue_ttm', 'Revenue (TTM)', 'money'], ['net_income_ttm', 'Net income (TTM)', 'money'],
  ['gross_margin', 'Gross margin', 'percent'], ['operating_margin', 'Operating margin', 'percent'], ['net_margin', 'Net margin', 'percent'],
  ['return_on_equity', 'Return on equity', 'percent'], ['return_on_assets', 'Return on assets', 'percent'], ['current_ratio', 'Current ratio', 'ratio'],
  ['debt_to_equity', 'Debt to equity', 'ratio'], ['revenue_growth', 'Revenue growth (YoY)', 'percent'], ['eps_growth', 'EPS growth (YoY)', 'percent'],
];

/** 1.23T, 456.7B, 89.0M, or the number itself when small. */
export function compact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  const size = Math.abs(value);
  for (const [limit, suffix] of [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']] as const) {
    if (size >= limit) return `${(value / limit).toFixed(size >= limit * 100 ? 0 : 2)}${suffix}`;
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function ratioText(value: number | null | undefined, kind: 'money' | 'ratio' | 'percent'): string {
  if (value === null || value === undefined) return '—';
  if (kind === 'money') return compact(value);
  if (kind === 'percent') return `${(value * 100).toFixed(1)}%`;
  return value.toFixed(2);
}

function periodLabel(end: string, quarterly: boolean): string {
  const date = new Date(`${end}T00:00:00Z`);
  return quarterly
    ? date.toLocaleDateString(undefined, { month: 'short', year: 'numeric', timeZone: 'UTC' })
    : `FY ${date.getUTCFullYear()}`;
}

function Bars({ rows, quarterly }: { rows: Row[]; quarterly: boolean }) {
  const values = rows.flatMap((row) => [row.values.revenue ?? 0, row.values.net_income ?? 0]);
  const max = Math.max(1, ...values.map(Math.abs));
  return (
    <div className="trading-financials-bars" role="img" aria-label="Revenue and net income by period">
      {rows.map((row) => (
        <div key={row.end} className="trading-financials-bar-group" title={`${periodLabel(row.end, quarterly)}: revenue ${compact(row.values.revenue)}, net income ${compact(row.values.net_income)}`}>
          <div className="trading-financials-bar-pair">
            <i className="is-revenue" style={{ height: `${(Math.abs(row.values.revenue ?? 0) / max) * 100}%` }} />
            <i className={(row.values.net_income ?? 0) < 0 ? 'is-loss' : 'is-income'} style={{ height: `${(Math.abs(row.values.net_income ?? 0) / max) * 100}%` }} />
          </div>
          <small>{periodLabel(row.end, quarterly)}</small>
        </div>
      ))}
    </div>
  );
}

export function TradingFinancials({ instrumentId }: { instrumentId: string }) {
  const [tab, setTab] = useState<Tab>('overview');
  const [quarterly, setQuarterly] = useState(false);
  const isStock = instrumentId.toLowerCase().startsWith('equity:');
  const query = useQuery({
    queryKey: ['trading', 'financials', instrumentId],
    queryFn: () => unwrapLabelled(api.GET('/api/trading/fundamentals', { params: { query: { instrument_id: instrumentId } } }), 'Financials') as Promise<Financials>,
    enabled: isStock,
    staleTime: 60 * 60_000,
    retry: false,
  });
  if (!isStock) return <p className="trading-financials">Financial statements are for US stocks.</p>;
  if (query.isLoading) return <p className="trading-financials" role="status">Loading SEC filings…</p>;
  if (query.isError || !query.data) return <p className="trading-financials" role="alert">{query.error instanceof Error ? query.error.message : 'The financial statements could not load.'}</p>;
  const data = query.data;
  const statements = (quarterly ? data.quarterly : data.annual) as unknown as Record<string, Row[]>;
  const labels = data.labels ?? {};
  return (
    <section className="trading-financials" aria-label="Financials">
      <header>
        <strong>{data.name || data.ticker}</strong>
        <span>{[data.sector, data.industry].filter(Boolean).join(' · ')}</span>
        <label><input type="checkbox" checked={quarterly} onChange={(event) => setQuarterly(event.target.checked)} />Quarterly</label>
      </header>
      <nav role="tablist" aria-label="Financials sections">
        {([['overview', 'Overview'], ['income', 'Income statement'], ['balance', 'Balance sheet'], ['cash_flow', 'Cash flow']] as const).map(([value, label]) => (
          <button key={value} type="button" role="tab" aria-selected={tab === value} onClick={() => setTab(value)}>{label}</button>
        ))}
      </nav>
      {tab === 'overview' ? (
        <>
          <dl className="trading-financials-ratios">
            {RATIOS.map(([key, label, kind]) => <div key={key}><dt>{label}</dt><dd>{ratioText(data.ratios[key], kind)}</dd></div>)}
          </dl>
          <Bars rows={(statements.income ?? []).slice(-8)} quarterly={quarterly} />
        </>
      ) : (
        <div className="trading-financials-table">
          <table aria-label={tab === 'income' ? 'Income statement' : tab === 'balance' ? 'Balance sheet' : 'Cash flow'}>
            <thead><tr><th>{data.currency}</th>{(statements[tab] ?? []).map((row) => <th key={row.end}>{periodLabel(row.end, quarterly)}</th>)}</tr></thead>
            <tbody>
              {ROWS[tab].filter((key) => (statements[tab] ?? []).some((row) => row.values[key] !== undefined)).map((key) => (
                <tr key={key}>
                  <th scope="row">{labels[key] ?? key}</th>
                  {(statements[tab] ?? []).map((row) => {
                    const value = row.values[key];
                    return <td key={row.end} className={value !== undefined && value < 0 ? 'is-negative' : undefined}>{value === undefined ? '—' : PER_SHARE.has(key) ? value.toFixed(2) : compact(value)}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <footer>Source: {data.source}. Ratios use the trailing twelve months and the last close{data.price ? ` (${data.price.toLocaleString()})` : ''}.</footer>
    </section>
  );
}
