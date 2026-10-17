import './drawings/TradingDrawingOverlay.css';
import { TradingIndicatorBackgroundOverlay } from './TradingIndicatorBackgroundOverlay';
import { TradingVolumeProfileOverlay } from './TradingVolumeProfileOverlay';
import { TradingCompareSymbolDialog } from './TradingCompareSymbolDialog';
import './TradingChartOverlayLayout.css';
import './TradingChartRangeTooltip.css';
import './TradingChartTimeControls.css';
import type { TradingChartPanelProps } from './tradingChartPanelModel';
import { useTradingChartPanel } from './useTradingChartPanel';
import { ChartPanelHeader } from './TradingChartPanelHeader';
import { ChartPanelIndicatorPanes, ChartPanelReplayMarkers, ChartPanelScaleControls } from './TradingChartPanelStage';
import { ChartPanelLegend, ChartPanelPaneControls } from './TradingChartPanelLegend';
import { ChartPanelOverlays, ChartPanelContextMenu } from './TradingChartPanelOverlays';
import { ChartPanelFooter } from './TradingChartPanelFooter';
import { useTradingChartPanelCommands } from './commands/useTradingChartPanelCommands';
import { useTradingDrawingCommands } from './commands/useTradingDrawingCommands';
import { useTradingOrderHotkeys } from './commands/useTradingOrderHotkeys';
import { TradingBarCountdown } from './TradingChartWorkflowControls';
import { TradingChartEventMarkers } from './TradingChartEventMarkers';

export function TradingChartPanel(props: TradingChartPanelProps) {
  const ws = useTradingChartPanel(props);
  useTradingChartPanelCommands(ws);
  useTradingDrawingCommands(ws);
  useTradingOrderHotkeys({ active: ws.active, adapter: ws.adapter, instrumentId: ws.instrumentId, lastPrice: () => { const close = Number(ws.latest?.close); return Number.isFinite(close) ? close : null; } });
  const {
    active, adapter, chartFocusMode, chartId, chartPanning, chartQuery, compareDialogOpen, comparisons,
    barCountdownVisible, drawingTool, eventMarkersOn, handleReplayStageClick, handleStageContextMenu, handleStageDoubleClick,
    handleStagePointerLeave, handleStagePointerMove, hostRef, indicatorError, instrumentId, interval, latest,
    onActivate, onOpenMarketDataSettings, onUpdateComparisons,
    panelRef, panningIndicatorPane, replayMode, setCompareDialogOpen, streamError, streamStatus,
    visibleIndicatorOutputs,
  } = ws;
  return (
    <article
      ref={panelRef}
      className={`trading-chart-panel${active ? ' active' : ''}${chartFocusMode ? ' is-chart-focus-mode' : ''}${replayMode && active ? ' replay-active' : ''}`}
      data-chart-id={chartId}
      data-stream-status={streamStatus}
      onPointerDown={onActivate}
      aria-label={`${chartId}${active ? ', active chart' : ''}`}
    >
      <ChartPanelHeader ws={ws} />
      <div
        className={`trading-chart-stage${replayMode && active ? ' is-replay-mode' : ''}`}
        onClickCapture={handleReplayStageClick}
        onContextMenu={handleStageContextMenu}
        onDoubleClick={handleStageDoubleClick}
        onPointerMove={handleStagePointerMove}
        onPointerLeave={handleStagePointerLeave}
      >
        <div role="group" ref={hostRef} className={`trading-chart-canvas${drawingTool === 'cursor' && !replayMode ? ' is-pan-ready' : ''}${chartPanning ? ' is-grabbing' : ''}`} data-panning-indicator={panningIndicatorPane ?? undefined} aria-label={`${instrumentId} ${interval} chart`} />
        {adapter ? <TradingIndicatorBackgroundOverlay adapter={adapter} outputs={visibleIndicatorOutputs} /> : null}
        {adapter ? <TradingVolumeProfileOverlay adapter={adapter} outputs={visibleIndicatorOutputs} /> : null}
        {adapter && barCountdownVisible ? <TradingBarCountdown adapter={adapter} bar={latest} interval={interval} /> : null}
        {adapter && eventMarkersOn ? <TradingChartEventMarkers adapter={adapter} instrumentId={instrumentId} interval={interval} replayMode={replayMode} barsRevision={chartQuery.data} /> : null}
        <ChartPanelIndicatorPanes ws={ws} />
        <ChartPanelReplayMarkers ws={ws} />
        <ChartPanelScaleControls ws={ws} />
        <ChartPanelLegend ws={ws} />
        <ChartPanelPaneControls ws={ws} />
        <ChartPanelOverlays ws={ws} />
        <ChartPanelContextMenu ws={ws} />
      </div>
      <TradingCompareSymbolDialog
        open={compareDialogOpen}
        currentInstrumentId={instrumentId}
        existingInstrumentIds={comparisons.map((comparison) => comparison.instrumentId)}
        onAdd={(instrument, placement) => {
          if (instrument.instrument_id === instrumentId) return;
          onUpdateComparisons(comparisons.some((comparison) => comparison.instrumentId === instrument.instrument_id)
            ? comparisons.map((comparison) => comparison.instrumentId === instrument.instrument_id ? { ...comparison, placement } : comparison)
            : [...comparisons, { instrumentId: instrument.instrument_id, placement, visible: true }]);
        }}
        onClose={() => setCompareDialogOpen(false)}
      />
      {chartQuery.isLoading ? <div className="trading-chart-state">Loading historical bars…</div> : null}
      {chartQuery.error ? (
        <div className="trading-chart-state error">
          <span>{chartQuery.error.message}</span>
          {/(CoinMarketCap|FRED) API key/.test(chartQuery.error.message) && onOpenMarketDataSettings ? (
            <button type="button" onClick={onOpenMarketDataSettings}>Open market-data settings</button>
          ) : null}
        </div>
      ) : null}
      {streamError ? <div className="trading-chart-state error">{streamError}</div> : null}
      {indicatorError ? <div className="trading-chart-state error">Indicator calculation failed: {indicatorError}</div> : null}
      <ChartPanelFooter ws={ws} />
    </article>
  );
}

