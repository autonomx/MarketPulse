import type { CoreIndicatorInstance } from './indicators/coreIndicators';

export function TradingIndicatorObjectToolbar({
  indicator,
  x,
  y,
  docked,
  onToggleVisibility,
  onSettings,
  onSourceCode,
  onResetView,
  onRemove,
  onDismiss,
}: {
  indicator: CoreIndicatorInstance;
  x: number;
  y: number;
  docked: boolean;
  onToggleVisibility: () => void;
  onSettings: () => void;
  onSourceCode: () => void;
  onResetView: () => void;
  onRemove: () => void;
  onDismiss: () => void;
}) {
  const label = `${indicator.id.toUpperCase()} ${indicator.period}`;
  const visible = indicator.visible !== false;
  return (
    <div
      className="trading-indicator-object-toolbar"
      style={{ left: Math.max(8, x + 12), top: Math.max(8, y - 38) }}
      role="toolbar"
      aria-label={`${label} indicator options`}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <span className="trading-indicator-object-toolbar-drag" aria-hidden="true">⋮⋮</span>
      <strong>{label}</strong>
      <button
        type="button"
        aria-label={`${visible ? 'Hide' : 'Show'} ${label}`}
        aria-pressed={visible}
        title={`${visible ? 'Hide' : 'Show'} ${label}`}
        onClick={onToggleVisibility}
      >
        {visible ? '◉' : '○'}
      </button>
      <button type="button" aria-label={`Open ${label} settings`} title="Settings" onClick={onSettings}>⚙</button>
      <button type="button" aria-label={`Open ${label} source code`} title="Source code" onClick={onSourceCode}>{'{}'}</button>
      {docked ? <button type="button" aria-label={`Reset ${label} scale and center`} title="Reset scale and center" onClick={onResetView}>↺</button> : null}
      <button type="button" className="trading-indicator-object-toolbar-delete" aria-label={`Remove ${label}`} title="Remove indicator" onClick={onRemove}>×</button>
      <button type="button" className="trading-indicator-object-toolbar-dismiss" aria-label={`Close ${label} options`} title="Close" onClick={onDismiss}>⋯</button>
    </div>
  );
}
