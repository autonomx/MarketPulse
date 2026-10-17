import { INTRABAR_REQUIREMENT, isIntrabarIndicatorId } from './indicators/intrabarIndicators';
import { createPortal } from 'react-dom';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { CoreIndicatorId, CoreIndicatorInstance } from './indicators/coreIndicators';
import { AUTO_CHART_PATTERN_DEFINITIONS, isAutoChartPatternId } from './indicators/autoPatterns';
import {
  TRADINGVIEW_BUILTIN_DEFINITIONS,
  tradingViewBuiltInDefaultPeriod,
} from './indicators/tradingViewBuiltIns';
import {
  classifyIndicatorCatalogEntry,
  indicatorAvailabilityMatches,
  indicatorMarketMatches,
  type IndicatorAvailabilityFilter,
  type IndicatorCategory,
  type IndicatorCategoryFilter,
  type IndicatorMarket,
  type IndicatorMarketFilter,
} from './indicators/indicatorCatalogFilters';
import { externalIndicatorRequirement, isExternalIndicatorId } from './indicators/externalIndicatorData';
import { useTradingCommand } from './commands/useTradingCommands';
import './TradingIndicatorManager.css';
import { PRODUCT_NAME } from '../../shared/productIdentity';

type PickerTab = 'indicators' | 'strategies' | 'profiles' | 'patterns';
type PickerSection = 'favorites' | 'my-scripts' | 'technicals' | 'fundamentals' | 'top' | 'trending';

type IndicatorDefinition = {
  id: CoreIndicatorId;
  name: string;
  author: string;
  boosts: string;
  section: 'technicals';
  kind: 'indicator' | 'profile' | 'pattern';
  available: boolean;
  requirement?: string;
  markets: IndicatorMarket[];
  category: IndicatorCategory;
};

type UnclassifiedIndicatorDefinition = Omit<IndicatorDefinition, 'markets' | 'category'>;

function indicatorDefinition(definition: UnclassifiedIndicatorDefinition): IndicatorDefinition {
  return {
    ...definition,
    ...classifyIndicatorCatalogEntry(definition.name, definition.kind),
  };
}

const omnixIndicatorDefinitions: IndicatorDefinition[] = [
  indicatorDefinition({ id: 'bull-market-band', name: 'Bull Market Support Band (20w SMA, 21w EMA)', author: 'zkdev', boosts: '5.3 K', section: 'technicals', kind: 'indicator', available: true }),
  indicatorDefinition({ id: 'death-cross', name: 'Death Cross - 200 MA / 50 Cross Checker', author: 'MexPayne', boosts: '879', section: 'technicals', kind: 'indicator', available: true }),
  indicatorDefinition({ id: 'ema-stack', name: 'EMA 9, 21, 50, 200', author: 'edufelarcon', boosts: '756', section: 'technicals', kind: 'indicator', available: true }),
  indicatorDefinition({ id: 'fair-value-gap', name: 'Fair Value Gap [LuxAlgo]', author: 'LuxAlgo', boosts: '24.5 K', section: 'technicals', kind: 'indicator', available: true }),
  indicatorDefinition({ id: 'golden-cross', name: 'Golden Cross', author: 'MichMexTrade', boosts: '229', section: 'technicals', kind: 'indicator', available: true }),
  indicatorDefinition({ id: 'ideal-bb', name: 'IDEAL BB with MA (With Alerts)', author: 'rautadarsh123', boosts: '9.9 K', section: 'technicals', kind: 'indicator', available: true }),
  indicatorDefinition({ id: 'log-macd', name: 'Logarithmic Moving Average Convergence Divergence', author: 'chemmist', boosts: '882', section: 'technicals', kind: 'indicator', available: true }),
  indicatorDefinition({ id: 'macd-dema', name: 'MACD DEMA', author: 'ToFFF', boosts: '6.5 K', section: 'technicals', kind: 'indicator', available: true }),
  indicatorDefinition({ id: 'swing-liquidity', name: 'Swing Levels and Liquidity - By Leviathan', author: 'LeviathanCapital', boosts: '11.2 K', section: 'technicals', kind: 'indicator', available: true }),
  indicatorDefinition({ id: 'volume-profile', name: 'Volume Profile', author: 'kv4coins', boosts: '23.3 K', section: 'technicals', kind: 'profile', available: true }),
];

const tradingViewIndicatorDefinitions: IndicatorDefinition[] = TRADINGVIEW_BUILTIN_DEFINITIONS.map((definition) => {
  const externalRequirement = externalIndicatorRequirement(definition.id) ?? (isIntrabarIndicatorId(definition.id) ? INTRABAR_REQUIREMENT : undefined);
  const external = isExternalIndicatorId(definition.id) || isIntrabarIndicatorId(definition.id);
  return indicatorDefinition({
    id: definition.id as CoreIndicatorId,
    name: definition.name,
    // Our own implementations: the catalog follows TradingView's for parity, but the product never names TradingView.
    author: isIntrabarIndicatorId(definition.id) ? 'Intrabar data' : external ? 'External market data' : 'Built-in',
    boosts: definition.available ? 'Built-in' : external ? 'Live data' : 'Needs data',
    section: 'technicals',
    // Like TradingView, candlestick patterns are listed with the chart patterns.
    kind: definition.name === 'All Candlestick Patterns' ? 'pattern' : 'indicator',
    available: definition.available || external,
    requirement: externalRequirement ?? definition.requirement,
  });
});

const patternDefinitions: IndicatorDefinition[] = AUTO_CHART_PATTERN_DEFINITIONS.map((definition) => indicatorDefinition({
  id: definition.id,
  name: definition.name,
  author: PRODUCT_NAME,
  boosts: 'Built-in',
  section: 'technicals',
  kind: 'pattern',
  available: true,
}));

const indicatorDefinitions: IndicatorDefinition[] = [
  ...tradingViewIndicatorDefinitions,
  ...omnixIndicatorDefinitions,
  ...patternDefinitions,
];

const sectionGroups: Array<{ label: string; sections: Array<{ id: PickerSection; label: string; icon: string }> }> = [
  {
    label: 'PERSONAL',
    sections: [
      { id: 'favorites', label: 'Favorites', icon: '★' },
      { id: 'my-scripts', label: 'My scripts', icon: '♙' },
    ],
  },
  {
    label: 'BUILT-IN',
    sections: [
      { id: 'technicals', label: 'Technicals', icon: '⌁' },
      { id: 'fundamentals', label: 'Fundamentals', icon: '▥' },
    ],
  },
  {
    label: 'COMMUNITY',
    sections: [
      { id: 'top', label: 'Top', icon: '↗' },
      { id: 'trending', label: 'Trending', icon: '♨' },
    ],
  },
];

const marketFilterOptions: Array<{ id: IndicatorMarketFilter; label: string }> = [
  { id: 'all', label: 'All markets' },
  { id: 'stocks', label: 'Stocks' },
  { id: 'crypto', label: 'Crypto' },
  { id: 'derivatives', label: 'Derivatives' },
  { id: 'universal', label: 'Universal only' },
];

const categoryFilterOptions: Array<{ id: IndicatorCategoryFilter; label: string }> = [
  { id: 'all', label: 'All categories' },
  { id: 'trend', label: 'Trend' },
  { id: 'momentum', label: 'Momentum' },
  { id: 'volatility', label: 'Volatility' },
  { id: 'volume', label: 'Volume & flow' },
  { id: 'levels', label: 'Levels & structure' },
  { id: 'breadth', label: 'Market breadth' },
  { id: 'on-chain', label: 'On-chain' },
  { id: 'derivatives', label: 'Derivatives data' },
  { id: 'fundamentals', label: 'Fundamentals' },
  { id: 'other', label: 'Other' },
];

const availabilityFilterOptions: Array<{ id: IndicatorAvailabilityFilter; label: string }> = [
  { id: 'all', label: 'All availability' },
  { id: 'ready', label: 'Ready now' },
  { id: 'data-required', label: 'Data required' },
];

const marketLabels: Record<IndicatorMarket, string> = {
  universal: 'Universal',
  stocks: 'Stocks',
  crypto: 'Crypto',
  derivatives: 'Derivatives',
};

const categoryLabels: Record<IndicatorCategory, string> = {
  trend: 'Trend',
  momentum: 'Momentum',
  volatility: 'Volatility',
  volume: 'Volume',
  levels: 'Levels',
  breadth: 'Breadth',
  'on-chain': 'On-chain',
  derivatives: 'Derivatives',
  fundamentals: 'Fundamentals',
  other: 'Other',
};

function indicatorPeriod(indicators: readonly CoreIndicatorInstance[], id: CoreIndicatorId): number {
  const configured = indicators.find((indicator) => indicator.id === id)?.period;
  if (configured !== undefined) return configured;
  const tradingViewDefault = tradingViewBuiltInDefaultPeriod(id);
  if (tradingViewDefault !== null) return tradingViewDefault;
  return isAutoChartPatternId(id) ? 3
    : id === 'rsi' || id === 'atr' || id === 'rsi-divergence' || id === 'stochastic-rsi' ? 14
      : id === 'macd' || id === 'log-macd' || id === 'macd-dema' ? 9
        : id === 'death-cross' || id === 'golden-cross' ? 50
          : id === 'ema-stack' ? 9
            : id === 'volume-profile' ? 100
              : id === 'swing-liquidity' ? 5
                : id === 'fair-value-gap' ? 3
                  : id === 'ideal-bb' ? 120
                    : 20;
}

export function TradingIndicatorManager({
  indicators,
  onToggle,
}: {
  indicators: CoreIndicatorInstance[];
  onToggle: (id: CoreIndicatorId) => void;
}) {
  const enabledCount = indicators.filter((indicator) => indicator.enabled).length;
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [tab, setTab] = useState<PickerTab>('indicators');
  const [section, setSection] = useState<PickerSection>('technicals');
  const [marketFilter, setMarketFilter] = useState<IndicatorMarketFilter>('all');
  const [categoryFilter, setCategoryFilter] = useState<IndicatorCategoryFilter>('all');
  const [availabilityFilter, setAvailabilityFilter] = useState<IndicatorAvailabilityFilter>('all');
  const [favoriteIds, setFavoriteIds] = useState<Set<CoreIndicatorId>>(
    () => new Set(indicatorDefinitions.map((definition) => definition.id)),
  );
  useTradingCommand('chart.indicators', () => setOpen(true), () => !open);

  useEffect(() => {
    if (!open) return undefined;
    const frame = window.requestAnimationFrame(() => searchRef.current?.focus());
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [open]);

  const enabledById = useMemo(
    () => new Map(indicators.map((indicator) => [indicator.id, indicator.enabled])),
    [indicators],
  );
  const filteredDefinitions = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return indicatorDefinitions.filter((definition) => {
      if (tab === 'strategies') return false;
      if (tab === 'patterns' && definition.kind !== 'pattern') return false;
      if (tab === 'profiles' && definition.kind !== 'profile') return false;
      // Preserve the existing one-click Volume Profile path while also exposing
      // the dedicated Profiles tab. Pattern studies stay isolated in Patterns.
      if (tab === 'indicators' && definition.kind === 'pattern') return false;
      if (section === 'favorites' && !favoriteIds.has(definition.id)) return false;
      if (section === 'fundamentals' && definition.category !== 'fundamentals') return false;
      if (!['favorites', 'technicals', 'fundamentals'].includes(section)) return false;
      if (!indicatorMarketMatches(definition.markets, marketFilter)) return false;
      if (categoryFilter !== 'all' && definition.category !== categoryFilter) return false;
      if (!indicatorAvailabilityMatches(definition.available, availabilityFilter)) return false;
      if (!normalizedQuery) return true;
      const searchable = [
        definition.name,
        definition.author,
        definition.category,
        ...definition.markets,
        definition.requirement ?? '',
      ].join(' ').toLowerCase();
      return searchable.includes(normalizedQuery);
    });
  }, [availabilityFilter, categoryFilter, favoriteIds, marketFilter, query, section, tab]);

  const toggleFavorite = (id: CoreIndicatorId) => {
    setFavoriteIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectIndicator = (definition: IndicatorDefinition) => {
    if (!definition.available) return;
    onToggle(definition.id);
    setOpen(false);
    triggerRef.current?.focus();
  };

  const resetFilters = () => {
    setQuery('');
    setMarketFilter('all');
    setCategoryFilter('all');
    setAvailabilityFilter('all');
  };
  const hasActiveFilters = Boolean(query.trim()) || marketFilter !== 'all' || categoryFilter !== 'all' || availabilityFilter !== 'all';

  const emptyCopy = tab === 'strategies'
    ? ['No strategies available yet', 'Strategy templates will appear here as they are added.']
    : tab === 'profiles'
      ? ['No profiles found', 'Try another search or filter.']
      : tab === 'patterns'
        ? ['No chart patterns found', 'Try another search or filter.']
        : ['No indicators found', 'Try another search, market, category, or availability filter.'];

  const picker = open && typeof document !== 'undefined' ? createPortal(
    <div className="trading-indicator-picker-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
      <section className="trading-indicator-picker" role="dialog" aria-modal="true" aria-labelledby="trading-indicator-picker-title">
        <header className="trading-indicator-picker-header">
          <h2 id="trading-indicator-picker-title">Indicators, metrics, and strategies</h2>
          <button type="button" className="trading-indicator-picker-close" aria-label="Close indicators" onClick={() => { setOpen(false); triggerRef.current?.focus(); }}>×</button>
        </header>

        <label className="trading-indicator-picker-search">
          <span className="trading-indicator-search-icon" aria-hidden="true" />
          <input ref={searchRef} type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search by name, market, category, or data requirement" aria-label="Search indicators" />
        </label>

        <div className="trading-indicator-picker-body">
          <aside className="trading-indicator-picker-sidebar" aria-label="Indicator categories">
            {sectionGroups.map((group) => (
              <div key={group.label} className="trading-indicator-picker-sidebar-group">
                <span className="trading-indicator-picker-sidebar-heading">{group.label}</span>
                {group.sections.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={section === item.id ? 'active' : undefined}
                    aria-pressed={section === item.id}
                    onClick={() => setSection(item.id)}
                  >
                    <span className="trading-indicator-picker-sidebar-icon" aria-hidden="true">{item.icon}</span>
                    <span>{item.label}</span>
                  </button>
                ))}
              </div>
            ))}
          </aside>

          <main className="trading-indicator-picker-results">
            <div className="trading-indicator-picker-tabs" role="tablist" aria-label="Indicator types">
              {(['indicators', 'strategies', 'profiles', 'patterns'] as PickerTab[]).map((item) => (
                <button key={item} type="button" role="tab" aria-selected={tab === item} className={tab === item ? 'active' : undefined} onClick={() => setTab(item)}>
                  {item[0].toUpperCase() + item.slice(1)}
                </button>
              ))}
            </div>
            <div role="group" className="trading-indicator-picker-filters" aria-label="Indicator filters">
              <label>
                <span>Market</span>
                <select aria-label="Market filter" value={marketFilter} onChange={(event) => setMarketFilter(event.target.value as IndicatorMarketFilter)}>
                  {marketFilterOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
                </select>
              </label>
              <label>
                <span>Category</span>
                <select aria-label="Category filter" value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value as IndicatorCategoryFilter)}>
                  {categoryFilterOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
                </select>
              </label>
              <label>
                <span>Availability</span>
                <select aria-label="Availability filter" value={availabilityFilter} onChange={(event) => setAvailabilityFilter(event.target.value as IndicatorAvailabilityFilter)}>
                  {availabilityFilterOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
                </select>
              </label>
              <div className="trading-indicator-picker-filter-summary">
                <span aria-live="polite">{filteredDefinitions.length} results</span>
                <button type="button" onClick={resetFilters} disabled={!hasActiveFilters}>Reset</button>
              </div>
            </div>
            <p className="trading-indicator-picker-filter-hint">Stocks, Crypto, and Derivatives include universal technical indicators plus market-specific studies. Use Universal only to hide specialized data series.</p>
            <div className="trading-indicator-picker-columns" aria-hidden="true"><span>NAME</span><span>AUTHOR</span><span>BOOSTS</span></div>
            <div className="trading-indicator-picker-list" role="group" aria-label={tab === 'patterns' ? 'Auto chart patterns' : 'Technical indicators'}>
              {filteredDefinitions.map((definition) => {
                const period = indicatorPeriod(indicators, definition.id);
                const enabled = enabledById.get(definition.id) ?? false;
                const buttonLabel = ['atr', 'bollinger', 'ema', 'macd', 'rsi', 'sma', 'vwap'].includes(definition.id)
                  ? `${definition.id.toUpperCase()} ${period}`
                  : definition.name;
                return (
                  <div key={`${definition.kind}:${definition.id}:${definition.name}`} className={`trading-indicator-picker-row${enabled ? ' active' : ''}${definition.available ? '' : ' unavailable'}`} title={definition.requirement}>
                    <button type="button" className="trading-indicator-picker-star" aria-label={`${favoriteIds.has(definition.id) ? 'Remove' : 'Add'} ${definition.name} favorite`} aria-pressed={favoriteIds.has(definition.id)} onClick={() => toggleFavorite(definition.id)}>★</button>
                    <button type="button" className="trading-indicator-picker-item" aria-label={buttonLabel} aria-pressed={enabled} disabled={!definition.available} onClick={() => selectIndicator(definition)}>
                      <span className="trading-indicator-picker-name">
                        {definition.name}
                        <span className="trading-indicator-picker-tags" aria-hidden="true">
                          <em>{categoryLabels[definition.category]}</em>
                          {definition.markets.map((market) => <em key={market}>{marketLabels[market]}</em>)}
                        </span>
                        {enabled ? <small>ACTIVE</small> : !definition.available ? <small>DATA REQUIRED</small> : null}
                      </span>
                      <span className="trading-indicator-picker-author">{definition.author}</span>
                      <span className="trading-indicator-picker-boosts">{definition.boosts}</span>
                    </button>
                  </div>
                );
              })}
              {filteredDefinitions.length === 0 ? (
                <div className="trading-indicator-picker-empty">
                  <strong>{emptyCopy[0]}</strong>
                  <span>{emptyCopy[1]}</span>
                </div>
              ) : null}
            </div>
          </main>
        </div>
      </section>
    </div>,
    document.body,
  ) : null;

  return (
    <div className={`trading-indicator-manager${enabledCount > 0 ? ' has-active' : ''}`}>
      <button
        ref={triggerRef}
        type="button"
        aria-label="Indicators"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((current) => !current)}
      >
        <span className="trading-indicator-glyph" aria-hidden="true"><i /><i /><i /></span>
        <span>Indicators</span>
        <span className="trading-menu-caret" aria-hidden="true">⌄</span>
      </button>
      {picker}
    </div>
  );
}
