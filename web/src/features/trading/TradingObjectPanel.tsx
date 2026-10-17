/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { tradingApi } from './tradingApi';
import type { CanonicalInstrument, MarketBar } from './tradingTypes';
import { intervalCompactLabel } from './tradingIntervals';
import { useTradingStore } from './tradingStore';
import { drawingScopeId } from './drawings/drawingToolSettings';
import { useTradingDrawings } from './drawings/useTradingDrawings';
import type { TradingDrawing } from './drawings/drawingCommands';
import { drawingToolDefinition } from './drawings/tools/registry';
import { TradingObjectTreeDrawings } from './TradingObjectTreeDrawings';
import { indicatorOutputs, type CoreIndicatorId, type CoreIndicatorInstance, type IndicatorOutput } from './indicators/coreIndicators';
import './TradingObjectPanel.css';
import { chartPalette } from './chartPalette';

type ObjectPanelView = 'object-tree' | 'data-window';

const indicatorNames: Partial<Record<CoreIndicatorId, string>> = {
  atr: 'Average True Range',
  bollinger: 'Bollinger Bands',
  'bull-market-band': 'Bull Market Support Band',
  'death-cross': 'Death Cross',
  ema: 'Exponential Moving Average',
  'ema-stack': 'EMA Stack',
  'fair-value-gap': 'Fair Value Gap',
  'golden-cross': 'Golden Cross',
  'ideal-bb': 'IDEAL BB with MA',
  'log-macd': 'Log MACD',
  'macd-dema': 'MACD DEMA',
  macd: 'Moving Average Convergence Divergence',
  rsi: 'Relative Strength Index',
  'rsi-divergence': 'RSI Divergence',
  sma: 'Simple Moving Average',
  'stochastic-rsi': 'Stochastic RSI',
  'swing-liquidity': 'Swing Levels and Liquidity',
  'volume-profile': 'Volume Profile',
  vwap: 'Volume Weighted Average Price',
};

const indicatorColors = [chartPalette.blue, chartPalette.orange, chartPalette.pink, chartPalette.teal, chartPalette.yellow, chartPalette.violet];

function displaySymbol(instrument: CanonicalInstrument | undefined, instrumentId: string): string {
  return instrument?.display_symbol ?? instrumentId.split(':').at(-1)?.replace('-', '') ?? instrumentId;
}

function displayIndicatorName(indicator: CoreIndicatorInstance): string {
  return indicatorNames[indicator.id] ?? indicator.id.toUpperCase();
}

function formatNumber(value: unknown, maximumFractionDigits = 2): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '—';
  return parsed.toLocaleString(undefined, { maximumFractionDigits, minimumFractionDigits: maximumFractionDigits >= 2 ? 2 : 0 });
}

function formatVolume(value: unknown): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '—';
  if (Math.abs(parsed) >= 1_000_000_000) return `${(parsed / 1_000_000_000).toFixed(2)}B`;
  if (Math.abs(parsed) >= 1_000_000) return `${(parsed / 1_000_000).toFixed(2)}M`;
  if (Math.abs(parsed) >= 1_000) return `${(parsed / 1_000).toFixed(2)}K`;
  return formatNumber(parsed, 0);
}

function formatDate(value: string | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, { weekday: 'short', day: '2-digit', month: 'short', year: '2-digit' }).format(date);
}

function changeFromBars(bars: readonly MarketBar[]): { value: number | null; percent: number | null } {
  const latest = Number(bars.at(-1)?.close);
  const previous = Number(bars.at(-2)?.close);
  if (!Number.isFinite(latest) || !Number.isFinite(previous)) return { value: null, percent: null };
  const value = latest - previous;
  return { value, percent: previous === 0 ? null : value / previous * 100 };
}

function changeClass(value: number | null): string {
  if (value == null) return '';
  return value >= 0 ? 'is-positive' : 'is-negative';
}

function CandleIcon() {
  return (
    <svg className="trading-object-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7 3v18M4.5 7h5v8h-5zM17 3v18M14.5 9h5v9h-5z" />
    </svg>
  );
}

function DrawingIcon({ drawing }: { drawing: TradingDrawing }) {
  const icon = drawingToolDefinition(drawing.toolType)?.icon;
  if (icon) return <svg className="trading-object-icon" viewBox="0 0 24 24" aria-hidden="true"><path d={icon} /></svg>;
  return (
    <svg className="trading-object-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 19 19 4" />
      <circle cx="4" cy="19" r="2.2" />
      <circle cx="19" cy="4" r="2.2" />
    </svg>
  );
}

function EyeIcon({ hidden }: { hidden: boolean }) {
  return hidden
    ? <svg className="trading-object-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m4 4 16 16M10.6 5.2A10.6 10.6 0 0 1 12 5c5 0 8.4 4.2 9.5 7-0.5 1.3-1.4 2.9-2.8 4.2M6.7 7.1C4.9 8.5 3.4 10.6 2.5 12c1.1 2.8 4.5 7 9.5 7 1 0 1.9-.2 2.8-.5" /></svg>
    : <svg className="trading-object-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M2.5 12S6 5 12 5s9.5 7 9.5 7S18 19 12 19s-9.5-7-9.5-7Z" /><circle cx="12" cy="12" r="2.7" /></svg>;
}

function TrashIcon() {
  return <svg className="trading-object-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14M9 7V4h6v3M7 7l1 14h8l1-14M10 10v8M14 10v8" /></svg>;
}

function TradingObjectTree({
  instrument,
  instrumentId,
  sessionId,
  interval,
  indicators,
  onSetIndicators,
  onOpenPineScript,
}: {
  instrument?: CanonicalInstrument;
  instrumentId: string;
  sessionId?: string;
  interval: string;
  indicators: CoreIndicatorInstance[];
  onSetIndicators: (indicators: CoreIndicatorInstance[]) => void;
  onOpenPineScript: (id: CoreIndicatorId) => void;
}) {
  const activeChartId = useTradingStore((state) => state.activeChartId);
  const syncDrawings = useTradingStore((state) => state.drawingToolSettings.syncDrawings);
  // The active chart's drawings: its own when drawings don't sync between charts (TVP-3.8).
  const drawings = useTradingDrawings(instrumentId, drawingScopeId(sessionId, activeChartId, syncDrawings));
  const [chartExpanded, setChartExpanded] = useState(true);
  const [indicatorsExpanded, setIndicatorsExpanded] = useState(true);
  const symbol = displaySymbol(instrument, instrumentId);
  const venue = instrument?.venue ?? 'Market';
  const enabledIndicators = indicators.filter((indicator) => indicator.enabled);

  const toggleIndicatorVisibility = (id: CoreIndicatorId) => {
    onSetIndicators(indicators.map((indicator) => indicator.id === id ? { ...indicator, visible: indicator.visible === false } : indicator));
  };
  const removeIndicator = (id: CoreIndicatorId) => {
    onSetIndicators(indicators.map((indicator) => indicator.id === id ? { ...indicator, enabled: false } : indicator));
  };

  return (
    <div className="trading-object-tree" data-status={drawings.status}>
      <div role="group" className="trading-object-toolbar" aria-label="Object tree actions">
        <button type="button" aria-label="Delete all drawings" title="Delete all drawings" onClick={() => drawings.removeAll()}><TrashIcon /></button>
        <span>{drawings.state.drawings.length + enabledIndicators.length} objects</span>
        <span className="trading-object-status">{drawings.status === 'saving' ? 'Saving…' : drawings.status === 'conflict' ? 'Conflict' : drawings.status === 'read-only' ? 'Read-only (newer version)' : ''}</span>
      </div>
      <div className="trading-object-scroll">
        <section className="trading-object-group">
          <button type="button" className="trading-object-group-header" aria-expanded={chartExpanded} onClick={() => setChartExpanded((value) => !value)}>
            <span className="trading-object-chevron" aria-hidden="true">{chartExpanded ? '⌄' : '›'}</span>
            <CandleIcon />
            <strong>{symbol} · {venue}, {intervalCompactLabel(interval)}</strong>
          </button>
          {chartExpanded ? (
            <>
              <TradingObjectTreeDrawings drawings={drawings} eyeIcon={(hidden) => <EyeIcon hidden={hidden} />} trashIcon={<TrashIcon />} drawingIcon={(drawing) => <DrawingIcon drawing={drawing} />} />
              {drawings.state.drawings.length === 0 || drawings.preservedCount > 0 ? (
                <ul className="trading-object-list">
                  {drawings.state.drawings.length === 0 ? <li className="trading-object-empty">No drawings on this chart</li> : null}
                  {drawings.preservedCount > 0 ? (
                    <li className="trading-object-empty">{drawings.preservedCount} unsupported {drawings.preservedCount === 1 ? 'drawing is' : 'drawings are'} kept but not shown</li>
                  ) : null}
                </ul>
              ) : null}
            </>
          ) : null}
        </section>

        <section className="trading-object-group trading-object-indicator-group">
          <button type="button" className="trading-object-group-header" aria-expanded={indicatorsExpanded} onClick={() => setIndicatorsExpanded((value) => !value)}>
            <span className="trading-object-chevron" aria-hidden="true">{indicatorsExpanded ? '⌄' : '›'}</span>
            <span className="trading-object-indicator-glyph" aria-hidden="true">∿</span>
            <strong>Indicators</strong>
            <small>{enabledIndicators.length}</small>
          </button>
          {indicatorsExpanded ? (
            <ul className="trading-object-list">
              {enabledIndicators.map((indicator, index) => {
                const name = `${displayIndicatorName(indicator)} ${indicator.period}`;
                const hidden = indicator.visible === false;
                return (
                  <li key={indicator.id} className={hidden ? 'is-hidden' : undefined}>
                    <button type="button" className="trading-object-row-main" onClick={() => toggleIndicatorVisibility(indicator.id)}>
                      <span className="trading-object-color-dot" style={{ background: indicatorColors[index % indicatorColors.length] }} />
                      <span>{name}<small>{hidden ? 'Hidden' : 'Indicator'}</small></span>
                    </button>
                    <div className="trading-object-row-actions">
                      <button type="button" aria-label={`${hidden ? 'Show' : 'Hide'} ${name}`} title={`${hidden ? 'Show' : 'Hide'} ${name}`} onClick={() => toggleIndicatorVisibility(indicator.id)}><EyeIcon hidden={hidden} /></button>
                      <button type="button" aria-label={`Open ${name} source code`} title={`Open ${name} source code`} onClick={() => onOpenPineScript(indicator.id)}>{'{}'}</button>
                      <button type="button" aria-label={`Delete ${name}`} title={`Delete ${name}`} onClick={() => removeIndicator(indicator.id)}><TrashIcon /></button>
                    </div>
                  </li>
                );
              })}
              {enabledIndicators.length === 0 ? <li className="trading-object-empty">No indicators on this chart</li> : null}
            </ul>
          ) : null}
        </section>
      </div>
    </div>
  );
}

type DataWindowIndicatorGroup = {
  id: CoreIndicatorId;
  title: string;
  outputs: IndicatorOutput[];
};

function TradingDataWindow({
  instrument,
  instrumentId,
  bindingId,
  interval,
  indicators,
}: {
  instrument?: CanonicalInstrument;
  instrumentId: string;
  bindingId: string | null;
  interval: string;
  indicators: CoreIndicatorInstance[];
}) {
  const barsQuery = useQuery({
    // The same clock-aligned buckets as the chart, so data-window values match what is drawn.
    queryKey: ['trading', 'bars', instrumentId, bindingId, interval, 1_000, 'clock', true],
    queryFn: () => tradingApi.bars(instrumentId, interval, 1_000, bindingId, { alignment: 'clock', extendedHours: true }),
    enabled: Boolean(instrumentId),
    staleTime: 15_000,
  });
  const bars = (barsQuery.data?.bars ?? []) as MarketBar[];
  const latest = bars.at(-1);
  const change = changeFromBars(bars);
  const indicatorGroups = useMemo<DataWindowIndicatorGroup[]>(() => (
    indicators
      .filter((indicator) => indicator.enabled && indicator.visible !== false)
      .map((indicator) => ({
        id: indicator.id,
        title: `${displayIndicatorName(indicator)} (${indicator.period})`,
        outputs: indicatorOutputs(bars, indicator).filter((output) => output.points.length > 0),
      }))
      .filter((group) => group.outputs.length > 0)
  ), [bars, indicators]);
  const symbol = displaySymbol(instrument, instrumentId);
  const venue = instrument?.venue ?? 'Market';

  if (barsQuery.isLoading) return <div className="trading-data-window-state">Loading data window…</div>;
  if (barsQuery.error) return <div className="trading-data-window-state is-error">Data window unavailable</div>;

  return (
    <div className="trading-data-window">
      <div className="trading-data-window-date"><span>Date</span><strong>{formatDate(latest?.end_time ?? latest?.start_time)}</strong></div>
      <div className="trading-data-window-scroll">
        <section className="trading-data-window-group trading-data-window-symbol">
          <header><span className="trading-data-window-symbol-icon"><CandleIcon /></span><strong>{symbol} · {intervalCompactLabel(interval)} · {venue}</strong><span className="trading-data-window-eye" aria-hidden="true">◉</span></header>
          <dl>
            <div><dt>Open</dt><dd>{formatNumber(latest?.open)}</dd></div>
            <div><dt>High</dt><dd>{formatNumber(latest?.high)}</dd></div>
            <div><dt>Low</dt><dd>{formatNumber(latest?.low)}</dd></div>
            <div><dt>Close</dt><dd>{formatNumber(latest?.close)}</dd></div>
            <div><dt>Change</dt><dd className={changeClass(change.value)}>{change.value == null ? '—' : `${change.value >= 0 ? '+' : ''}${formatNumber(change.value)} (${change.percent == null ? '—' : `${change.percent >= 0 ? '+' : ''}${formatNumber(change.percent)}%`})`}</dd></div>
            <div><dt>Vol</dt><dd>{formatVolume(latest?.volume)}</dd></div>
            <div><dt>Last bar change</dt><dd className={changeClass(change.value)}>{change.value == null ? '—' : `${change.value >= 0 ? '+' : ''}${formatNumber(change.value)} (${change.percent == null ? '—' : `${change.percent >= 0 ? '+' : ''}${formatNumber(change.percent)}%`})`}</dd></div>
          </dl>
        </section>

        {indicatorGroups.map((group) => (
          <section key={group.id} className="trading-data-window-group trading-data-window-indicator">
            <header><span className="trading-data-window-line-icon" aria-hidden="true">⌁</span><strong>{group.title}</strong></header>
            <dl>
              {group.outputs.map((output, index) => {
                const point = output.points.at(-1);
                return <div key={output.key}><dt>{output.title}</dt><dd style={{ color: output.color ?? indicatorColors[index % indicatorColors.length] }}>{formatNumber(point?.value)}</dd></div>;
              })}
            </dl>
          </section>
        ))}
        {indicatorGroups.length === 0 ? <div className="trading-data-window-state">No active indicator values</div> : null}
      </div>
    </div>
  );
}

export function TradingObjectPanel({
  sessionId,
  instruments,
  activeInstrumentId,
  bindingId,
  interval,
  indicators,
  onSetIndicators,
  onOpenPineScript,
}: {
  sessionId?: string;
  instruments: CanonicalInstrument[];
  activeInstrumentId: string;
  bindingId: string | null;
  interval: string;
  indicators: CoreIndicatorInstance[];
  onSetIndicators: (indicators: CoreIndicatorInstance[]) => void;
  onOpenPineScript: (id: CoreIndicatorId) => void;
}) {
  const [view, setView] = useState<ObjectPanelView>('object-tree');
  const instrument = instruments.find((item) => item.instrument_id === activeInstrumentId);
  return (
    <div role="group" className="trading-object-panel" aria-label="Chart objects and data window">
      <nav className="trading-object-panel-tabs" role="tablist" aria-label="Chart side views">
        <button type="button" role="tab" aria-selected={view === 'object-tree'} onClick={() => setView('object-tree')}>Object tree</button>
        <button type="button" role="tab" aria-selected={view === 'data-window'} onClick={() => setView('data-window')}>Data window</button>
      </nav>
      {view === 'object-tree' ? <TradingObjectTree instrument={instrument} instrumentId={activeInstrumentId} sessionId={sessionId} interval={interval} indicators={indicators} onSetIndicators={onSetIndicators} onOpenPineScript={onOpenPineScript} /> : null}
      {view === 'data-window' ? <TradingDataWindow instrument={instrument} instrumentId={activeInstrumentId} bindingId={bindingId} interval={interval} indicators={indicators} /> : null}
    </div>
  );
}
