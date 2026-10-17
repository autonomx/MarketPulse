import { indicatorPaneId, type TradingChartAdapter, type TradingIndicatorPaneGeometry } from '../chart/chartAdapter';
import type { IndicatorOutput } from '../indicators/coreIndicators';
import './ScriptTablesOverlay.css';

type PaneBox = { top: number; height: number; width: number };

/** The plot area of an output's pane (the price pane, or its script's own pane), in the chart stage's pixels. */
function paneBox(output: IndicatorOutput, adapter: TradingChartAdapter, panes: readonly TradingIndicatorPaneGeometry[]): PaneBox | null {
  try {
    const width = adapter.indicatorPlotWidth();
    const id = indicatorPaneId(output);
    if (id === null) {
      const height = adapter.mainPaneHeight();
      return height ? { top: 0, height, width } : null;
    }
    const pane = panes.find((item) => item.id === id);
    return pane ? { top: pane.top, height: pane.height, width } : null;
  } catch {
    return null; // the chart is gone
  }
}

/**
 * Omnix Script tables (table.new, TVP-11.1) over the chart, each in its script's pane (the price pane for an overlay
 * script) at its position (top_right, middle_center, ...), with its cells' text and colours; tables at the same
 * position stack in the order they come. Tables don't take the pointer, so the chart stays usable under them.
 */
export function ScriptTablesOverlay({ outputs, adapter, panes }: {
  outputs: readonly IndicatorOutput[];
  adapter: TradingChartAdapter | null;
  panes: readonly TradingIndicatorPaneGeometry[];
}) {
  if (!adapter) return null;
  const groups = new Map<string, { box: PaneBox; slots: Map<string, IndicatorOutput[]> }>();
  for (const output of outputs) {
    if (output.kind !== 'table' || !output.table || output.visible === false) continue;
    const box = paneBox(output, adapter, panes);
    if (!box) continue;
    const paneKey = indicatorPaneId(output) ?? '';
    const group = groups.get(paneKey) ?? { box, slots: new Map<string, IndicatorOutput[]>() };
    groups.set(paneKey, group);
    group.slots.set(output.table.position, [...(group.slots.get(output.table.position) ?? []), output]);
  }
  if (groups.size === 0) return null;
  return (
    <div className="trading-script-tables">
      {[...groups].map(([paneKey, { box, slots }]) => (
        <div key={paneKey || 'price'} className="trading-script-table-pane" style={{ top: box.top, height: box.height, width: box.width }}>
          {[...slots].map(([position, tables]) => (
            <div key={position} className={`trading-script-table-slot is-${position.replace('_', '-')}`}>
              {tables.map((output) => <ScriptTable key={output.key} output={output} />)}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function ScriptTable({ output }: { output: IndicatorOutput }) {
  const table = output.table!;
  return (
    <table
      className="trading-script-table"
      aria-label={output.title}
      style={{ ...(table.background ? { background: table.background } : {}), ...(table.border ? { borderColor: table.border } : {}) }}
    >
      <tbody>
        {table.rows.map((row, rowIndex) => (
          <tr key={rowIndex}>
            {row.map((cell, columnIndex) => (
              <td key={columnIndex} style={{ ...(cell?.color ? { color: cell.color } : {}), ...(cell?.background ? { background: cell.background } : {}) }}>
                {cell?.text ?? ''}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
