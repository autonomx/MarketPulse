import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { TradingChartPanel } from './TradingChartPanel';
import { TradingChartSynchronization } from './chart/chartSynchronization';
import { installTradingChartViewportPersistence } from './chart/chartViewportPersistence';
import { currentTradingWorkspaceScopeId } from './persistence/useTradingWorkspacePersistence';
import { useTradingStore, type TradingLayout } from './tradingStore';
import type { CoreIndicatorId } from './indicators/coreIndicators';
import { useTradingCommand } from './commands/useTradingCommands';

installTradingChartViewportPersistence();

export function tradingGridColumns(layout: TradingLayout, chartCount: number): number {
  if (layout === 'rows-2' || layout === 'rows-3' || layout === 'rows-4') return 1;
  if (layout === 'main-left-3' || layout === 'main-right-3' || layout === 'main-top-3' || layout === 'main-bottom-3') return 2;
  const forced = {
    'columns-1': 1,
    'columns-2': 2,
    'columns-3': 3,
    'columns-4': 4,
  } as const;
  if (layout !== 'auto') return forced[layout];
  return Math.max(1, Math.min(4, Math.ceil(Math.sqrt(Math.max(1, chartCount)))));
}

export function TradingChartGrid({
  paperAccountId,
  onOpenSymbolSearch,
  onOpenPineScript,
  onOpenMarketDataSettings,
}: {
  paperAccountId?: string | null;
  onOpenSymbolSearch: (chartId: string) => void;
  onOpenPineScript: (id: CoreIndicatorId) => void;
  onOpenMarketDataSettings?: () => void;
}) {
  const layout = useTradingStore((state) => state.layout);
  const charts = useTradingStore((state) => state.charts);
  const activeTabId = useTradingStore((state) => state.activeTabId);
  const activeChartId = useTradingStore((state) => state.activeChartId);
  const links = useTradingStore((state) => state.links);
  const setActiveChart = useTradingStore((state) => state.setActiveChart);
  const updateChart = useTradingStore((state) => state.updateChart);
  const toggleIndicator = useTradingStore((state) => state.toggleIndicator);
  const setIndicators = useTradingStore((state) => state.setIndicators);
  const toggleIndicatorVisibility = useTradingStore((state) => state.toggleIndicatorVisibility);
  const updateIndicator = useTradingStore((state) => state.updateIndicator);
  const moveIndicator = useTradingStore((state) => state.moveIndicator);
  const [focusedChartId, setFocusedChartId] = useState<string | null>(null);
  const synchronization = useMemo(() => new TradingChartSynchronization(), []);
  const workspaceScopeId = currentTradingWorkspaceScopeId();
  const columns = tradingGridColumns(layout, charts.length);
  const style = { '--trading-grid-columns': columns } as CSSProperties;
  const focusedChart = focusedChartId === null ? null : charts.find((chart) => chart.chartId === focusedChartId) ?? null;
  const visibleCharts = focusedChart ? [focusedChart] : charts;

  useEffect(() => {
    synchronization.setLinks({ crosshair: links.crosshair, visibleRange: links.visibleRange });
  }, [links.crosshair, links.visibleRange, synchronization]);

  useEffect(() => () => synchronization.dispose(), [synchronization]);

  useEffect(() => {
    if (focusedChartId !== null && !charts.some((chart) => chart.chartId === focusedChartId)) setFocusedChartId(null);
  }, [charts, focusedChartId]);

  useEffect(() => {
    setFocusedChartId(null);
  }, [activeTabId, workspaceScopeId]);

  // A maximised layout shows the active chart, so switching charts (Tab) moves the maximised view.
  useEffect(() => {
    setFocusedChartId((current) => current === null ? current : activeChartId);
  }, [activeChartId]);

  useTradingCommand('layout.maximizeChart', () => setFocusedChartId((current) => current === null ? activeChartId : null));

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented && focusedChartId !== null) setFocusedChartId(null);
    };
    window.addEventListener('keydown', handleEscape);
    return () => window.removeEventListener('keydown', handleEscape);
  }, [focusedChartId]);

  return (
    <section
      className={`trading-chart-grid layout-${layout}${focusedChart ? ' is-chart-focus-mode' : ''}`}
      style={style}
      aria-label={`${charts.length}-chart Trading layout with ${columns} column${columns === 1 ? '' : 's'}`}
    >
      {visibleCharts.map((chart) => (
        <div key={chart.chartId} className="trading-chart-grid-cell">
          <TradingChartPanel
            key={`${workspaceScopeId}:${activeTabId}:${chart.chartId}`}
            sessionId={activeTabId}
            chartId={chart.chartId}
            chartNumber={charts.findIndex((item) => item.chartId === chart.chartId) + 1}
            instrumentId={chart.instrumentId}
            bindingId={chart.bindingId}
            interval={chart.interval}
            chartType={chart.chartType}
            indicators={chart.indicators}
            comparisons={chart.comparisons ?? []}
            active={chart.chartId === activeChartId}
            chartFocusMode={focusedChart?.chartId === chart.chartId}
            onChartFocusChange={(focused) => setFocusedChartId(focused ? chart.chartId : null)}
            onActivate={() => setActiveChart(chart.chartId)}
            onOpenSymbolSearch={() => onOpenSymbolSearch(chart.chartId)}
            onChangeInterval={(interval) => updateChart(chart.chartId, { interval })}
            onChangeChartType={(chartType) => updateChart(chart.chartId, { chartType })}
            onToggleIndicator={(id) => toggleIndicator(chart.chartId, id)}
            onClearIndicators={() => setIndicators(chart.chartId, [])}
            onToggleIndicatorVisibility={(id) => toggleIndicatorVisibility(chart.chartId, id)}
            onUpdateIndicator={(id, patch) => updateIndicator(chart.chartId, id, patch)}
            onMoveIndicator={(id, direction) => moveIndicator(chart.chartId, id, direction)}
            onUpdateComparisons={(comparisons) => updateChart(chart.chartId, { comparisons })}
            onOpenPineScript={(id) => { setActiveChart(chart.chartId); onOpenPineScript(id); }}
            onOpenMarketDataSettings={onOpenMarketDataSettings}
            synchronization={synchronization}
            paperAccountId={paperAccountId}
          />
        </div>
      ))}
    </section>
  );
}
