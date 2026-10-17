import { isExternalIndicatorId } from './indicators/externalIndicatorData';
import { isIntrabarIndicatorId } from './indicators/intrabarIndicators';
import { indicatorSourceChoices } from './indicators/indicatorSources';
import { indicatorUsesSeparatePane } from './indicators/coreIndicators';
import { isScriptIndicatorId, scriptIndicatorName } from './scripts/scriptIndicators';
import { TradingIndicatorPaneControls } from './TradingIndicatorPaneControls';
import { TradingIndicatorObjectToolbar } from './TradingIndicatorObjectToolbar';
import { TradingIndicatorSettings } from './TradingIndicatorSettings';
import { comparisonPercent } from './tradingChartPanelModel';
import type { TradingChartPanelModel } from './useTradingChartPanel';

/** The legend: indicators and comparisons. */
export function ChartPanelLegend({ ws }: { ws: TradingChartPanelModel }) {
  const {
    comparisons, indicatorLegendCollapsed, legendComparisons, legendCount, legendIndicators,
    onOpenPineScript, onToggleIndicator, onToggleIndicatorVisibility, onUpdateComparisons, setCompareDialogOpen,
    setIndicatorLegendCollapsed, setSettingsIndicator,
  } = ws;
  return (
    <>
      {legendCount > 0 ? (
        <div
          className={`trading-overlay-indicator-controls trading-indicator-legend${indicatorLegendCollapsed ? ' is-collapsed' : ''}`}
          role="group"
          aria-label="Indicator legend"
          onPointerDown={(event) => event.stopPropagation()}
        >
          {!indicatorLegendCollapsed ? legendIndicators.map((indicator) => {
            const label = scriptIndicatorName(indicator) ?? `${indicator.id.toUpperCase()} ${indicator.period}`;
            const visible = indicator.visible !== false;
            const docked = indicatorUsesSeparatePane(indicator.id);
            const kind = docked ? 'indicator' : 'overlay';
            return (
              <div key={indicator.id} className={`trading-overlay-indicator${visible ? ' active' : ' hidden'}`}>
                <span className="trading-overlay-indicator-label">{label}</span>
                <button
                  type="button"
                  aria-label={`${visible ? 'Hide' : 'Show'} ${label} ${kind}`}
                  aria-pressed={visible}
                  title={`${visible ? 'Hide' : 'Show'} ${label}`}
                  onClick={() => onToggleIndicatorVisibility(indicator.id)}
                >
                  {visible ? '◉' : '○'}
                </button>
                <button
                  type="button"
                  aria-label={`Open ${label} settings`}
                  title={`Open ${label} settings`}
                  onClick={() => setSettingsIndicator(indicator)}
                >
                  ⚙
                 </button>
                 <button
                   type="button"
                   aria-label={`Open ${label} source code`}
                   title={`Open ${label} source code`}
                   onClick={() => onOpenPineScript(indicator.id)}
                 >
                   {'{}'}
                 </button>
                 <button
                   type="button"
                   className="trading-overlay-indicator-delete"
                  aria-label={`Delete ${label} ${kind}`}
                  title={`Delete ${label}`}
                  onClick={() => onToggleIndicator(indicator.id)}
                >
                  ×
                </button>
              </div>
            );
          }) : null}
          {!indicatorLegendCollapsed ? legendComparisons.map((comparison) => (
            <div key={comparison.instrumentId} className={`trading-overlay-indicator${comparison.visible ? ' active' : ' hidden'}`}>
              <span className="trading-overlay-indicator-label">{comparison.label} · {comparisonPercent(comparison.bars)}</span>
              <button
                type="button"
                aria-label={`${comparison.visible ? 'Hide' : 'Show'} ${comparison.label}`}
                aria-pressed={comparison.visible}
                title={`${comparison.visible ? 'Hide' : 'Show'} ${comparison.label}`}
                onClick={() => onUpdateComparisons(comparisons.map((item) => item.instrumentId === comparison.instrumentId ? { ...item, visible: !comparison.visible } : item))}
              >{comparison.visible ? '◉' : '○'}</button>
              <button
                type="button"
                aria-label={`Open comparison settings for ${comparison.label}`}
                title="Add or change comparison"
                onClick={() => setCompareDialogOpen(true)}
              >⚙</button>
              <button
                type="button"
                className="trading-overlay-indicator-delete"
                aria-label={`Remove comparison ${comparison.label}`}
                title={`Remove ${comparison.label}`}
                onClick={() => onUpdateComparisons(comparisons.filter((item) => item.instrumentId !== comparison.instrumentId))}
              >×</button>
            </div>
          )) : null}
          <button
            type="button"
            className="trading-indicator-legend-toggle"
            aria-label={indicatorLegendCollapsed ? 'Expand indicator legend' : 'Collapse indicator legend'}
            aria-expanded={!indicatorLegendCollapsed}
            title={indicatorLegendCollapsed ? 'Expand indicator legend' : 'Collapse indicator legend'}
            onClick={() => setIndicatorLegendCollapsed((collapsed) => !collapsed)}
          >
            <span aria-hidden="true">{indicatorLegendCollapsed ? '⌄' : '⌃'}</span>
            {indicatorLegendCollapsed ? <span className="trading-indicator-legend-count">{legendCount}</span> : null}
          </button>
        </div>
      ) : null}
    </>
  );
}

/** Indicator pane controls, the selected-indicator toolbar and indicator settings. */
export function ChartPanelPaneControls({ ws }: { ws: TradingChartPanelModel }) {
  const {
    closeIndicator, fullscreenIndicator, fullscreenMainPane, hoveredIndicatorPane, indicatorPaneGeometry,
    minimizedIndicators, onMoveIndicator, onOpenPineScript, onToggleIndicator, onToggleIndicatorVisibility,
    onUpdateIndicator, paneIndicators, resetIndicatorPaneView, selectedIndicator, selectedIndicatorConfig,
    setSelectedIndicator, setSettingsIndicator, settingsIndicator, toggleFullscreenIndicator,
    toggleMinimizedIndicator,
    instrumentId,
  } = ws;
  return (
    <>
      {paneIndicators.filter((indicator) => !fullscreenMainPane && (!fullscreenIndicator || indicator.id === fullscreenIndicator)).map((indicator, index) => {
        const geometry = indicatorPaneGeometry.find((item) => item.id === indicator.id);
        if (!geometry) return null;
        return (
          <TradingIndicatorPaneControls
            key={indicator.id}
            indicator={indicator}
            geometry={geometry}
            minimized={minimizedIndicators.has(indicator.id)}
            fullscreen={fullscreenIndicator === indicator.id}
            hovered={hoveredIndicatorPane === indicator.id}
            canMoveUp={index > 0}
            canMoveDown={index < paneIndicators.length - 1}
            onToggleMinimized={() => toggleMinimizedIndicator(indicator.id)}
            onToggleFullscreen={() => void toggleFullscreenIndicator(indicator.id)}
            onResetView={() => resetIndicatorPaneView(indicator.id)}
            onSettings={() => setSettingsIndicator(indicator)}
            onSourceCode={() => onOpenPineScript(indicator.id)}
            onMove={(direction) => onMoveIndicator(indicator.id, direction)}
            onClose={() => closeIndicator(indicator.id)}
          />
        );
      })}
      {selectedIndicator && selectedIndicatorConfig ? (
        <TradingIndicatorObjectToolbar
          indicator={selectedIndicatorConfig}
          x={selectedIndicator.x}
          y={selectedIndicator.y}
          docked={indicatorUsesSeparatePane(selectedIndicatorConfig.id)}
          onToggleVisibility={() => onToggleIndicatorVisibility(selectedIndicatorConfig.id)}
          onSettings={() => setSettingsIndicator(selectedIndicatorConfig)}
          onSourceCode={() => onOpenPineScript(selectedIndicatorConfig.id)}
          onResetView={() => resetIndicatorPaneView(selectedIndicatorConfig.id)}
          onRemove={() => {
            setSelectedIndicator(null);
            onToggleIndicator(selectedIndicatorConfig.id);
          }}
          onDismiss={() => setSelectedIndicator(null)}
        />
      ) : null}
      {settingsIndicator ? (
        <TradingIndicatorSettings
          indicator={settingsIndicator}
          instrumentId={instrumentId}
          sourceChoices={indicatorSourceChoices(settingsIndicator, ws.indicators, ws.indicatorOutputs, (id) => !isExternalIndicatorId(id) && !isIntrabarIndicatorId(id) && !isScriptIndicatorId(id))}
          onApply={(patch) => onUpdateIndicator(settingsIndicator.id, patch)}
          onClose={() => setSettingsIndicator(null)}
        />
      ) : null}
    </>
  );
}
