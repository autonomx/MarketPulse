import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { TradingChartAdapter } from '../chart/chartAdapter';
import type { IndicatorOutput } from '../indicators/coreIndicators';
import { ScriptTablesOverlay } from './ScriptTablesOverlay';

function table(key: string, position: string, text: string, pane: 0 | 1 = 0, visible = true): IndicatorOutput {
  return {
    key, title: `table ${key}`, pane, kind: 'table', points: [], visible,
    table: { position, rows: [[{ text, color: '#D1D4DC' }, null]], background: '#131722' },
  };
}

const adapter = { indicatorPlotWidth: () => 900, mainPaneHeight: () => 400 } as unknown as TradingChartAdapter;
const panes = [{ id: 'script-osc', paneIndex: 1, top: 400, height: 150 }];

describe('script tables over the chart (TVP-11.1)', () => {
  it("draws each table in its script's pane at its position, stacking tables that share one, and skips hidden ones", () => {
    const { container } = render(<ScriptTablesOverlay adapter={adapter} panes={panes} outputs={[
      table('script-band:a', 'top_right', 'RSI 42'), table('script-band:b', 'top_right', 'MACD'), table('script-band:c', 'bottom_left', 'Vol'),
      table('script-band:d', 'middle_center', 'off', 0, false), table('script-osc:t1', 'bottom_left', 'In its pane', 1),
      { key: 'line', title: 'Line', pane: 0, kind: 'line', points: [] },
    ]} />);
    const [price, osc] = Array.from(container.querySelectorAll<HTMLElement>('.trading-script-table-pane'));
    // The price pane's plot area for the overlay script, the script's own pane for the other.
    expect(price.style).toMatchObject({ top: '0px', height: '400px', width: '900px' });
    expect(osc.style).toMatchObject({ top: '400px', height: '150px', width: '900px' });
    const slots = price.querySelectorAll('.trading-script-table-slot');
    expect([...slots].map((slot) => slot.className)).toEqual(['trading-script-table-slot is-top-right', 'trading-script-table-slot is-bottom-left']);
    expect(slots[0].querySelectorAll('table')).toHaveLength(2);
    expect(osc.textContent).toBe('In its pane');
    expect(screen.getByRole('table', { name: 'table script-band:a' })).toHaveStyle({ background: '#131722' });
    expect(screen.getByText('RSI 42')).toHaveStyle({ color: '#D1D4DC' });
    expect(screen.queryByText('off')).toBeNull();
  });

  it('draws nothing without tables, a chart, or a pane for them', () => {
    expect(render(<ScriptTablesOverlay adapter={adapter} panes={[]} outputs={[]} />).container).toBeEmptyDOMElement();
    expect(render(<ScriptTablesOverlay adapter={null} panes={panes} outputs={[table('script-band:a', 'top_right', 'x')]} />).container).toBeEmptyDOMElement();
    expect(render(<ScriptTablesOverlay adapter={adapter} panes={[]} outputs={[table('script-gone:a', 'top_right', 'x', 1)]} />).container).toBeEmptyDOMElement();
  });
});
