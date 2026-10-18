/** What each trading data source feeds, so a user knows which key turns on which feature. */
import { SettingsSection } from './SettingsPrimitives';
import './DataSourcesOverview.css';

type Source = { name: string; uses: string };

const KEYED: Source[] = [
  { name: 'Alpaca', uses: 'Real-time US stock quotes for AUTO PAPER, option chains, dividends and splits, market breadth, and rebuilt gapper history in backtests.' },
  { name: 'CoinMarketCap', uses: 'Crypto market-cap symbols: TOTAL, TOTAL2, TOTAL3 and dominance symbols such as BTC.D and USDT.D.' },
  { name: 'FRED', uses: 'The economic calendar: US data releases (CPI, jobs, GDP, …) with their actual values.' },
  { name: 'Interactive Brokers', uses: 'Optional, through IB Gateway or the IBKR Web API. Live stock charts from one minute to 30 minutes while connected, and checks chart data and repairs gaps. Your IBKR username and password are never stored.' },
];

const BUILT_IN: Source[] = [
  { name: 'Yahoo Finance', uses: 'Stock, ETF and index charts and quotes, symbol search, premarket gappers and catalysts, and key statistics.' },
  { name: 'Stooq', uses: 'Long daily stock history.' },
  { name: 'SEC EDGAR', uses: 'Financial statements, company profiles, filings, and earnings dates.' },
  { name: 'Finviz', uses: 'The premarket gapper list.' },
  { name: 'Binance', uses: 'Crypto charts, the crypto heatmap, and futures funding, open interest and liquidations.' },
  { name: 'Coinbase, Kraken, Hyperliquid', uses: 'Charts for crypto pairs listed on those exchanges.' },
  { name: 'US Treasury', uses: 'Daily Treasury yields.' },
  { name: 'Blockchain.com', uses: 'Bitcoin network metrics.' },
];

function SourceList({ sources }: { sources: Source[] }) {
  return (
    <dl className="settings-data-sources">
      {sources.map((source) => (
        <div key={source.name}>
          <dt>{source.name}</dt>
          <dd>{source.uses}</dd>
        </div>
      ))}
    </dl>
  );
}

export function DataSourcesOverview() {
  return (
    <SettingsSection title="What each data source is for" description="Built-in sources work without setup. A key or connection below turns on the features listed beside it.">
      <h4 className="settings-data-sources-heading">Needs your key or connection</h4>
      <SourceList sources={KEYED} />
      <h4 className="settings-data-sources-heading">Built in, no key needed</h4>
      <SourceList sources={BUILT_IN} />
    </SettingsSection>
  );
}
