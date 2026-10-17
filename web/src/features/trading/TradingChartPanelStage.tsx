import { PriceScaleMode } from 'lightweight-charts';
import { TradingPriceScaleMenu } from './TradingPriceScaleMenu';
import { TradingYAxisControls } from './TradingYAxisControls';
import { rightOffsetOptions } from './tradingChartPanelModel';
import type { TradingChartPanelModel } from './useTradingChartPanel';

/** Separate indicator panes over the chart. */
export function ChartPanelIndicatorPanes({ ws }: { ws: TradingChartPanelModel }) {
  const {
    fullscreenIndicator, fullscreenMainPane, indicatorPaneGeometry, paneIndicators, resizingIndicatorPane,
    startIndicatorPaneResize,
  } = ws;
  return (
    <>
      {!fullscreenIndicator && !fullscreenMainPane ? paneIndicators.flatMap((indicator) => {
        const geometry = indicatorPaneGeometry.find((item) => item.id === indicator.id);
        if (!geometry || geometry.height <= 40) return [];
        return (['top', 'bottom'] as const).map((edge) => (
          <div
            key={`${indicator.id}-resize-${edge}`}
            className={`trading-indicator-pane-resize-handle ${edge}${resizingIndicatorPane === indicator.id ? ' is-resizing' : ''}`}
            style={{ top: `${edge === 'top' ? geometry.top : geometry.top + geometry.height}px` }}
            data-indicator-id={indicator.id}
            data-edge={edge}
            role="separator"
            aria-orientation="horizontal"
            aria-label={`Resize ${indicator.id.toUpperCase()} ${indicator.period} pane ${edge} border`}
            aria-valuemin={80}
            aria-valuenow={Math.round(geometry.height)}
            onPointerDown={(event) => startIndicatorPaneResize(event, indicator.id, edge)}
          />
        ));
      }) : null}
    </>
  );
}

/** Replay selection and position markers. */
export function ChartPanelReplayMarkers({ ws }: { ws: TradingChartPanelModel }) {
  const { active, replayChoosingStart, replayMarkerX, replayMode, replaySelectionIndex, replaySelectionX, replayStartBar } = ws;
  return (
    <>
      {replayChoosingStart && replaySelectionX !== null ? (
        <div
          className="trading-replay-future-overlay"
          style={{ left: `${replaySelectionX}px` }}
          aria-hidden="true"
        />
      ) : null}
      {replayMode && active && replayMarkerX !== null ? (
        <div className="trading-replay-marker" style={{ left: `${replayMarkerX}px` }} aria-hidden="true">
          <span>Replay start</span>
        </div>
      ) : null}
      {replayChoosingStart && replaySelectionX !== null ? (
        <div className="trading-replay-selection-divider" style={{ left: `${replaySelectionX}px` }} aria-hidden="true">
          <span>✂</span>
          <small>{replaySelectionIndex === null ? 'Select replay start' : 'Replay start'}</small>
        </div>
      ) : null}
      {replayMode && active ? (
        <div className="trading-replay-mode-hint" role="status">
          {replayStartBar ? `Replay starts ${new Date(replayStartBar.start_time).toLocaleString()}` : 'Click a candle to choose the replay start'}
        </div>
      ) : null}
    </>
  );
}

/** Price-scale and y-axis controls of the active chart. */
export function ChartPanelScaleControls({ ws }: { ws: TradingChartPanelModel }) {
  const {
    active, adapter, changeRightOffset, priceScaleCurrency, priceScaleHovered, priceScaleMenuOpen,
    priceScaleSettings, rightOffset, setPriceScaleCurrency, setPriceScaleMenuOpen, setPriceScaleSettings,
    setSettingsVisible,
  } = ws;
  return (
    <>
      {active && adapter ? (
        <>
          <TradingYAxisControls
            side={priceScaleSettings.side}
            currency={priceScaleCurrency}
            autoScale={priceScaleSettings.autoScale}
            logarithmic={priceScaleSettings.mode === 'logarithmic'}
            visible={priceScaleHovered}
            onCurrencyChange={setPriceScaleCurrency}
            onAutoFit={() => {
              adapter.setPriceScaleAutoScale(true);
              setPriceScaleSettings((current) => ({ ...current, autoScale: true }));
            }}
            onToggleLogarithmic={() => {
              const logarithmic = priceScaleSettings.mode !== 'logarithmic';
              adapter.setPriceScaleMode(logarithmic ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal);
              setPriceScaleSettings((current) => ({ ...current, mode: logarithmic ? 'logarithmic' : 'normal' }));
            }}
          />
          <button
            type="button"
            className="trading-price-scale-trigger"
            aria-label="Open price scale settings"
            aria-expanded={priceScaleMenuOpen}
            title="Price scale settings"
            onPointerDown={(event) => event.stopPropagation()}
            onContextMenu={(event) => event.stopPropagation()}
            onClick={() => setPriceScaleMenuOpen((value) => !value)}
          >
            ⋮
          </button>
          {priceScaleMenuOpen ? (
            <TradingPriceScaleMenu
              adapter={adapter}
              state={priceScaleSettings}
              onChange={(patch) => setPriceScaleSettings((current) => ({ ...current, ...patch }))}
              onClose={() => setPriceScaleMenuOpen(false)}
              onSettings={() => setSettingsVisible(true)}
              rightOffset={rightOffset}
              rightOffsetOptions={rightOffsetOptions}
              onRightOffsetChange={changeRightOffset}
            />
          ) : null}
        </>
      ) : null}
    </>
  );
}
