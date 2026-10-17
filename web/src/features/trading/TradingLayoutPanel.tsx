import { TradingDiagnosticsPanel } from './TradingDiagnosticsPanel';
import type { DrawingSnapMode } from './drawings/drawingCommands';
import type { TradingLayout, TradingLinkState } from './tradingStore';

const layouts: Array<{ id: TradingLayout; label: string; glyph: string }> = [
  { id: 'auto', label: 'Automatic grid', glyph: '▦' },
  { id: 'columns-1', label: 'One column', glyph: '▤' },
  { id: 'columns-2', label: 'Two columns', glyph: '▥' },
  { id: 'columns-3', label: 'Three columns', glyph: '▦' },
  { id: 'columns-4', label: 'Four columns', glyph: '▦' },
];

export function TradingLayoutPanel({
  layout,
  chartCount,
  minimumChartCount,
  maximumChartCount,
  links,
  snapMode,
  onSetLayout,
  onSetChartCount,
  onAddChart,
  onRemoveChart,
  onSetLink,
  onSetSnapMode,
  onDuplicateLayout,
}: {
  layout: TradingLayout;
  chartCount: number;
  minimumChartCount: number;
  maximumChartCount: number;
  links: TradingLinkState;
  snapMode: DrawingSnapMode;
  onSetLayout: (layout: TradingLayout) => void;
  onSetChartCount: (count: number) => void;
  onAddChart: () => void;
  onRemoveChart: () => void;
  onSetLink: (key: keyof TradingLinkState, enabled: boolean) => void;
  onSetSnapMode: (mode: DrawingSnapMode) => void;
  /** Copies the saved layout (workspace), its tabs and drawings into a new one (TVP-2.5). */
  onDuplicateLayout?: () => void;
}) {
  return (
    <section className="trading-layout-panel" aria-label="Trading layout settings">
      <div className="trading-chart-count-control">
        <div>
          <strong>Charts</strong>
          <small>Each chart keeps its own symbol and interval.</small>
        </div>
        <div>
          <button type="button" onClick={onRemoveChart} disabled={chartCount <= minimumChartCount} aria-label="Remove active chart">−</button>
          <select
            aria-label="Number of charts"
            value={chartCount}
            onChange={(event) => onSetChartCount(Number(event.target.value))}
          >
            {Array.from({ length: maximumChartCount - minimumChartCount + 1 }, (_, index) => minimumChartCount + index).map((count) => (
              <option key={count} value={count}>{count}</option>
            ))}
          </select>
          <button type="button" onClick={onAddChart} disabled={chartCount >= maximumChartCount} aria-label="Add chart">+</button>
        </div>
      </div>

      {onDuplicateLayout ? (
        <div className="trading-chart-count-control trading-layout-duplicate">
          <div>
            <strong>Layout</strong>
            <small>Copy this layout, its sessions and drawings.</small>
          </div>
          <button type="button" onClick={onDuplicateLayout}>Duplicate layout</button>
        </div>
      ) : null}

      <div className="trading-layout-options" role="radiogroup" aria-label="Grid columns">
        {layouts.map((item) => (
          <button
            key={item.id}
            type="button"
            role="radio"
            aria-checked={layout === item.id}
            className={layout === item.id ? 'active' : undefined}
            onClick={() => onSetLayout(item.id)}
          >
            <span aria-hidden="true">{item.glyph}</span>
            <small>{item.label}</small>
          </button>
        ))}
      </div>

      <fieldset>
        <legend>Chart linking</legend>
        {(Object.keys(links) as Array<keyof TradingLinkState>).map((key) => (
          <label key={key}>
            <input
              type="checkbox"
              checked={links[key]}
              onChange={(event) => onSetLink(key, event.target.checked)}
            />
            <span>{key === 'visibleRange' ? 'Link visible range' : key}</span>
          </label>
        ))}
      </fieldset>

      <label className="trading-layout-snap">
        Drawing snap
        <select
          aria-label="Drawing snap mode"
          value={snapMode}
          onChange={(event) => onSetSnapMode(event.target.value as DrawingSnapMode)}
        >
          {(['none', 'time', 'price', 'ohlc'] as DrawingSnapMode[]).map((item) => (
            <option key={item} value={item}>{item.toUpperCase()}</option>
          ))}
        </select>
      </label>

      <details className="trading-layout-diagnostics">
        <summary>Data diagnostics</summary>
        <TradingDiagnosticsPanel />
      </details>
    </section>
  );
}
