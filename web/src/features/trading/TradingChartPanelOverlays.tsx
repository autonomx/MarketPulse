import { DrawingAlertSync } from './DrawingAlertSync';
import { useMemo } from 'react';
import { alertIndicatorChoices } from './alertIndicatorSources';
import { useAlertIndicatorIds } from './useTradingAlerts';
import { TradingChartAlertOverlay } from './TradingChartAlertOverlay';
import { TradingPositionOverlay } from './TradingPositionOverlay';
import { TradingOrderLinesOverlay } from './TradingOrderLinesOverlay';
import { TradingPriceScalePlus } from './TradingPriceScalePlus';
import { TradingChartContextMenu } from './TradingChartContextMenu';
import { ScriptTablesOverlay } from './scripts/ScriptTablesOverlay';
import { TRADING_CHART_TYPE_OPTIONS, type TradingChartType } from './chart/chartAdapter';
import { drawingInstrumentOf } from './drawings/drawingInstrument';
import { TradingDrawingOverlay } from './drawings/TradingDrawingOverlay';
import { convertedPrice, indicatorContextLabel, isAlertIndicatorId, price, rightOffsetOptions } from './tradingChartPanelModel';
import type { TradingChartPanelModel } from './useTradingChartPanel';
import { ChartWorkflowSettings } from './TradingChartWorkflowControls';

/** Drawings, alerts, positions, the data table, object tree and settings. */
export function ChartPanelOverlays({ ws }: { ws: TradingChartPanelModel }) {
  const {
    active, adapter, alertPlacement, bars, bindingId, changeRightOffset, chartQuery, chartType, clearAlertPlacement,
    drawingSnapMode, drawingTool, drawings, drawingsHidden, toggleDrawingsHidden, drawingToolSettings, indicatorOutputs, indicatorPaneGeometry, indicators, instrumentId, interval, latest, latestClose, objectTreeVisible,
    onActivate, onChangeChartType, onToggleIndicator, openContextMenu, paperAccountId, priceScaleMultiplier,
    provenance, replayMode, resolvedBinding, rightOffset, setAlertPlacement, setDrawingTool, setObjectTreeVisible,
    setSettingsVisible, setTableVisible, settingsVisible, tableVisible,
  } = ws;
  const tickSize = drawingInstrumentOf(chartQuery.data?.instrument).tickSize;
  const alertIndicatorIds = useAlertIndicatorIds();
  const indicatorChoices = useMemo(() => alertIndicatorChoices(indicators, indicatorOutputs, alertIndicatorIds), [alertIndicatorIds, indicatorOutputs, indicators]);
  return (
    <>
      <TradingDrawingOverlay
        adapter={adapter}
        instrumentId={instrumentId}
        interval={interval}
        tool={active ? drawingTool : 'cursor'}
        snapMode={drawingSnapMode}
        drawings={drawings.state.drawings}
        selectedId={drawings.state.selectedId}
        onAdd={(drawing) => { if (drawingsHidden) toggleDrawingsHidden(); drawings.add(drawing); }}
        onSelect={(id) => { onActivate(); drawings.select(id); }}
        onToggleSelect={(id) => { onActivate(); drawings.toggleSelect(id); }}
        onCloneDrawings={drawings.clone}
        allHidden={drawingsHidden}
        allLocked={drawingToolSettings.lockAll}
        onMovePoint={drawings.movePoint}
        onEditDrawing={drawings.edit}
        instrument={drawingInstrumentOf(chartQuery.data?.instrument)}
        onTranslateDrawing={drawings.translate}
        onRemove={drawings.remove}
        onToolComplete={() => { if (!drawingToolSettings.stayInDrawingMode) setDrawingTool('cursor'); }}
        onAlertAtPoint={active ? (placement, indicatorId) => {
          const indicator = indicatorId
            ? indicators.find((item) => item.id === indicatorId && item.enabled)
            : undefined;
          const supportedIndicatorId = indicatorId && isAlertIndicatorId(indicatorId)
            ? indicatorId
            : undefined;
          setAlertPlacement({
            ...placement,
            ...(indicatorId ? { chartIndicatorId: indicatorId } : {}),
            ...(supportedIndicatorId ? { indicatorId: supportedIndicatorId } : {}),
            ...(indicator?.period !== undefined ? { indicatorPeriod: indicator.period } : {}),
          });
        } : undefined}
        onContextMenu={active ? openContextMenu : undefined}
      />
      <TradingChartAlertOverlay
        adapter={adapter}
        instrumentId={instrumentId}
        bindingId={provenance?.requested_binding ?? bindingId ?? resolvedBinding?.binding_id ?? null}
        interval={interval}
        latestPrice={latestClose}
        symbol={chartQuery.data?.instrument.display_symbol ?? instrumentId}
        placement={alertPlacement}
        onPlacementConsumed={clearAlertPlacement}
        indicatorChoices={indicatorChoices}
      />
      <DrawingAlertSync adapter={adapter} instrumentId={instrumentId} instrument={drawingInstrumentOf(chartQuery.data?.instrument)} drawings={drawings.state.drawings} active={active} replayMode={replayMode} />
      <TradingPositionOverlay adapter={adapter} accountId={paperAccountId} instrumentId={instrumentId} />
      <ScriptTablesOverlay outputs={indicatorOutputs} adapter={adapter} panes={indicatorPaneGeometry} />
      <TradingOrderLinesOverlay adapter={adapter} accountId={paperAccountId} instrumentId={instrumentId} tickSize={tickSize} disabled={replayMode} />
      {active ? (
        <TradingPriceScalePlus
          adapter={adapter}
          instrumentId={instrumentId}
          lastPrice={latestClose > 0 ? latestClose : null}
          tickSize={tickSize}
          onAddAlert={(price, y) => { if (latest) setAlertPlacement({ time: latest.start_time, price, x: 0, y, source: 'context-menu' }); }}
        />
      ) : null}
      {tableVisible ? (
        <div className="trading-chart-table-view" role="dialog" aria-label="Chart table view" onPointerDown={(event) => event.stopPropagation()}>
          <header><strong>Table view · {chartQuery.data?.instrument.display_symbol ?? instrumentId}</strong><button type="button" onClick={() => setTableVisible(false)} aria-label="Close table view">×</button></header>
          <table>
            <thead><tr><th>Time</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Volume</th></tr></thead>
            <tbody>{bars.slice(-12).reverse().map((bar) => <tr key={bar.start_time}><td>{new Date(bar.start_time).toLocaleString()}</td><td>{convertedPrice(bar.open, priceScaleMultiplier)}</td><td>{convertedPrice(bar.high, priceScaleMultiplier)}</td><td>{convertedPrice(bar.low, priceScaleMultiplier)}</td><td>{convertedPrice(bar.close, priceScaleMultiplier)}</td><td>{price(bar.volume)}</td></tr>)}</tbody>
          </table>
        </div>
      ) : null}
      {objectTreeVisible ? (
        <aside className="trading-chart-object-tree" role="dialog" aria-label="Object tree" onPointerDown={(event) => event.stopPropagation()}>
          <header><strong>Object tree</strong><button type="button" onClick={() => setObjectTreeVisible(false)} aria-label="Close object tree">×</button></header>
          <ul>
            {drawings.state.drawings.map((drawing) => <li key={drawing.drawingId}><span>{drawing.toolType}{drawing.locked ? ' · locked' : ''}</span><button type="button" onClick={() => { drawings.select(drawing.drawingId); setObjectTreeVisible(false); }}>Select</button></li>)}
            {indicators.filter((indicator) => indicator.enabled).map((indicator) => <li key={indicator.id}><span>{indicator.id.toUpperCase()} {indicator.period}</span><button type="button" onClick={() => onToggleIndicator(indicator.id)}>Remove</button></li>)}
            {drawings.state.drawings.length === 0 && indicators.every((indicator) => !indicator.enabled) ? <li><span>No chart objects</span></li> : null}
          </ul>
        </aside>
      ) : null}
      {settingsVisible ? (
        <aside className="trading-chart-settings" role="dialog" aria-label="Chart settings" onPointerDown={(event) => event.stopPropagation()}>
          <header><strong>Chart settings</strong><button type="button" onClick={() => setSettingsVisible(false)} aria-label="Close chart settings">×</button></header>
          <label>Chart type<select value={chartType} onChange={(event) => onChangeChartType(event.target.value as TradingChartType)}>{TRADING_CHART_TYPE_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
          <label>Right margin<select aria-label="Chart right margin" value={rightOffset} onChange={(event) => changeRightOffset(event.target.value)}>{rightOffsetOptions.map((offset) => <option key={offset} value={offset}>{offset === 0 ? 'None' : `${offset} bars`}</option>)}</select></label>
          <label>Snap mode<select value={drawingSnapMode} disabled><option>{drawingSnapMode}</option></select></label>
          <ChartWorkflowSettings ws={ws} />
        </aside>
      ) : null}
    </>
  );
}

/** The chart's context menu. */
export function ChartPanelContextMenu({ ws }: { ws: TradingChartPanelModel }) {
  const {
    adapterRef, applyChartTemplate, chartId, chartQuery, contextIndicator, contextMenu, contextMenuAlert, copyContextPrice,
    cursorLocked, drawings, indicators, instrumentId, onClearIndicators, pasteContextPrice, resetIndicatorPaneView,
    selectedRangeRef, setContextMenu, setCursorLocked, setObjectTreeVisible, setPriceScaleSettings,
    setSelectedRangeLabel, setSettingsIndicator, setSettingsVisible, setTableVisible, tableVisible,
  } = ws;
  return (
    <>
      {contextMenu ? (
        <>
          <div
            className="trading-chart-context-menu-dismiss-layer"
            aria-hidden="true"
            onPointerDown={() => setContextMenu(null)}
            onContextMenu={(event) => {
              event.preventDefault();
              event.stopPropagation();
            }}
          />
          <TradingChartContextMenu
            point={contextMenu}
            chartId={chartId}
            instrumentId={contextIndicator || contextMenu?.trendlinePoints?.length === 2 ? null : instrumentId}
            symbol={contextIndicator
              ? indicatorContextLabel(contextIndicator)
              : contextMenu?.trendlinePoints?.length === 2
                ? 'trendline'
                : (chartQuery.data?.instrument.display_symbol ?? instrumentId)}
            indicatorContext={Boolean(contextIndicator)}
            drawingCount={drawings.state.drawings.length}
            indicatorCount={indicators.filter((indicator) => indicator.enabled).length}
            cursorLocked={cursorLocked}
            tableVisible={tableVisible}
            onClose={() => setContextMenu(null)}
            onReset={() => {
              if (contextIndicator) {
                resetIndicatorPaneView(contextIndicator.id);
                return;
              }
              selectedRangeRef.current = null;
              adapterRef.current?.fitContent();
              setPriceScaleSettings((current) => ({ ...current, autoScale: true }));
              setSelectedRangeLabel('All');
            }}
            onCopyPrice={copyContextPrice}
            onPastePrice={pasteContextPrice}
            onAddAlert={contextMenuAlert}
            onToggleCursor={() => setCursorLocked((value) => !value)}
            onToggleTable={() => setTableVisible((value) => !value)}
            onObjectTree={() => setObjectTreeVisible(true)}
            onApplyTemplate={applyChartTemplate}
            onRemoveDrawings={() => drawings.removeAll()}
            onRemoveIndicators={onClearIndicators}
            onSettings={() => contextIndicator ? setSettingsIndicator(contextIndicator) : setSettingsVisible(true)}
          />
        </>
      ) : null}
    </>
  );
}
