import type { TradingIndicatorPaneGeometry } from './chart/chartAdapter';
import type { CoreIndicatorId } from './indicators/coreIndicators';
import { useTradingStore, type TradingIndicatorMove } from './tradingStore';
import { chartPalette } from './chartPalette';

export function TradingIndicatorPaneControls({
  indicator,
  geometry,
  minimized,
  fullscreen,
  hovered,
  canMoveUp,
  canMoveDown,
  onToggleMinimized,
  onToggleFullscreen,
  onResetView,
  onSettings,
  onSourceCode,
  onMove,
  onClose,
}: {
  indicator: { id: CoreIndicatorId; period: number };
  geometry: TradingIndicatorPaneGeometry;
  minimized: boolean;
  fullscreen: boolean;
  hovered: boolean;
  canMoveUp: boolean;
  canMoveDown: boolean;
  onToggleMinimized: () => void;
  onToggleFullscreen: () => void;
  onResetView: () => void;
  onSettings: () => void;
  onSourceCode: () => void;
  onMove: (direction: TradingIndicatorMove) => void;
  onClose: () => void;
}) {
  const drawingTool = useTradingStore((state) => state.drawingTool);
  // A drawing/alert tool owns the chart pointer until it completes. Removing
  // pane chrome from the DOM avoids stale hover state leaving an invisible
  // control above the drawing overlay and stealing the placement click.
  if (drawingTool !== 'cursor') return null;

  const label = `${indicator.id.toUpperCase()} ${indicator.period}`;
  return (
    <div
      className="trading-overlay-indicator-controls trading-indicator-pane-controls"
      style={{
        top: Math.max(2, geometry.top + 4),
        right: 64,
        left: 'auto',
        display: 'flex',
        minWidth: 'auto',
        padding: 2,
        borderColor: chartPalette.paneBorder,
        background: chartPalette.paneBackground,
        boxShadow: `0 5px 16px ${chartPalette.paneShadow}`,
        pointerEvents: hovered || minimized || fullscreen ? 'auto' : 'none',
      }}
      onPointerDown={(event) => event.stopPropagation()}
      role="group"
      aria-label={`${label} indicator controls`}
      data-indicator-id={indicator.id}
      data-minimized={minimized}
      data-fullscreen={fullscreen}
      data-hovered={hovered}
    >
      <span className="trading-indicator-pane-label">{label}</span>
      <button
        type="button"
        title={fullscreen ? `Exit fullscreen ${label} panel` : `Enter fullscreen ${label} panel`}
        aria-label={fullscreen ? `Exit fullscreen ${label} panel` : `Enter fullscreen ${label} panel`}
        aria-pressed={fullscreen}
        onClick={onToggleFullscreen}
      >
        {fullscreen ? '↙' : '⛶'}
      </button>
      <button type="button" title={`Move ${label} panel up`} aria-label={`Move ${label} panel up`} disabled={!canMoveUp} onClick={() => onMove('up')}>↑</button>
      <button type="button" title={`Move ${label} panel down`} aria-label={`Move ${label} panel down`} disabled={!canMoveDown} onClick={() => onMove('down')}>↓</button>
      <button type="button" title={`Reset ${label} scale and center`} aria-label={`Reset ${label} scale and center`} onClick={onResetView}>↺</button>
      <button
        type="button"
        title={minimized ? `Restore ${label} panel` : `Minimize ${label} panel`}
        aria-label={minimized ? `Restore ${label} panel` : `Minimize ${label} panel`}
        aria-expanded={!minimized}
        onClick={onToggleMinimized}
      >
        {minimized ? '⌃' : '⌄'}
      </button>
      <button type="button" title={`Open ${label} settings`} aria-label={`Open ${label} settings`} onClick={onSettings}>⚙</button>
      <button type="button" title={`Close ${label} panel`} aria-label={`Close ${label} panel`} onClick={onClose}>×</button>
      <button type="button" title={`Open ${label} source code`} aria-label={`Open ${label} source code`} onClick={onSourceCode}>{'{}'}</button>
    </div>
  );
}
