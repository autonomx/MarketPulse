import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CoreIndicatorInstance } from '../indicators/coreIndicators';
import type { MarketBar } from '../tradingTypes';

const tradingApi = vi.hoisted(() => ({ document: vi.fn() }));
const scriptsApi = vi.hoisted(() => ({ run: vi.fn() }));
vi.mock('../tradingApi', () => ({ tradingApi }));
vi.mock('./scriptsApi', () => ({ scriptsApi }));

const {
  calculateScriptIndicatorOutputs, forgetScriptSource, isScriptIndicatorId, scriptColor, scriptIdOf, scriptIndicatorInstance,
  scriptIndicatorName, scriptInputValues, scriptOutputs, scriptRunStatus, scriptTable, withScriptInput,
} = await import('./scriptIndicators');
const { parseIndicatorInstances } = await import('../persistence/workspaceDocument');

const START = Date.parse('2026-08-03T14:00:00Z');
function bars(count: number): MarketBar[] {
  return Array.from({ length: count }, (_, index) => ({
    instrument_id: 'equity:NASDAQ:AAPL', interval: '1m', start_time: new Date(START + index * 60_000).toISOString(),
    end_time: new Date(START + (index + 1) * 60_000).toISOString(), open: '100', high: String(102 + index), low: String(98 - index),
    close: String(100 + index), volume: '10', is_final: true, provider: 'fixture', received_at: '',
  }) as MarketBar);
}
// The server's times: isoformat with an offset, and one bar the chart doesn't have (older than its first).
const runTimes = ['2026-08-03T13:59:00+00:00', ...[0, 1, 2, 3].map((index) => new Date(START + index * 60_000).toISOString().replace('.000Z', '+00:00'))];

const instance = scriptIndicatorInstance('sabc', 'Mine', false, 2);

function result(overrides: Record<string, unknown> = {}) {
  return {
    declaration: { kind: 'indicator', title: 'Mine', overlay: false, precision: 3 }, inputs: [], plots: [], hlines: [], fills: [], drawings: [],
    alerts: [], logs: [], profile: [], bars: 5, seconds: 0.01, ...overrides,
  };
}

describe('script indicators (TVP-11.1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    forgetScriptSource('sabc');
  });

  it('names its script and keeps inputs in params', () => {
    expect(instance).toMatchObject({ id: 'script-sabc', params: { name: 'Mine', overlay: 0, revision: 2 } });
    expect(isScriptIndicatorId('script-sabc') && scriptIdOf('script-sabc') === 'sabc' && !isScriptIndicatorId('sma')).toBe(true);
    expect(scriptIndicatorName(instance)).toBe('Mine');
    const params = withScriptInput(withScriptInput(instance.params, 'Length', 14), 'Show', false);
    expect(scriptInputValues({ params })).toEqual({ Length: 14, Show: false });
    // Layouts keep script indicators.
    expect(parseIndicatorInstances([{ ...instance, params }])).toHaveLength(1);
    expect(parseIndicatorInstances([{ ...instance, id: 'script-a:b' }])).toBeNull();
  });

  it('reads Pine colours with transparency', () => {
    expect(scriptColor('#F23645CC')).toBe('rgba(242, 54, 69, 0.8)');
    expect(scriptColor({ color: '#2962FF' })).toBe('#2962FF');
    expect(scriptColor(null)).toBeNull();
  });

  it('maps plots, shapes, backgrounds, bar colours, levels and drawings onto the chart bars', () => {
    const mapped = scriptOutputs(instance, result({
      plots: [
        { index: 0, kind: 'plot', title: 'MA', options: { linewidth: 2 }, values: [9, 1, null, 3, 4], colors: [{ color: '#000000' }, { color: '#F23645' }, { color: '#F23645' }, null, { color: '#089981' }] },
        { index: 1, kind: 'plot', title: 'Hist', options: { style: 'plot.style_histogram', color: '#2962FF' }, values: [0, 1, 2, 3, 4], colors: null },
        { index: 2, kind: 'plotshape', title: 'Cross', options: { style: 'shape.triangleup', location: 'location.belowbar', text: 'X' }, values: [true, false, true, false, false], colors: null },
        { index: 3, kind: 'bgcolor', title: 'bg', options: {}, values: [null, '#FDD83533', null, null, '#FDD835'], colors: null },
        { index: 4, kind: 'barcolor', title: 'bars', options: {}, values: [null, null, '#089981', null, null], colors: null },
        { index: 5, kind: 'alertcondition', title: 'High', options: {}, values: [false, false, false, false, true], colors: null },
        { index: 6, kind: 'plotcandle', title: 'Candles', options: {}, values: [], colors: null },
      ],
      hlines: [{ price: 101, title: 'Mid', linestyle: 'hline.style_dotted' }],
      fills: [{ from: 0, to: 1 }],
      drawings: [
        { kind: 'line', id: 1, fields: { x1: 4, y1: 110, x2: 1, y2: 100, xloc: 'xloc.bar_index', color: '#2196F3' } },
        { kind: 'box', id: 2, fields: { left: 1, top: 105, right: 3, bottom: 95, xloc: 'xloc.bar_index' } },
        { kind: 'label', id: 3, fields: { x: 4, y: 104, text: 'last', xloc: 'xloc.bar_index', color: '#F23645' } },
        { kind: 'table', id: 4, fields: {} },
      ],
    }), runTimes, bars(4));
    const byKey = Object.fromEntries(mapped.outputs.map((output) => [output.key.replace('script-sabc:', ''), output]));
    // The run's first bar is older than the chart's: dropped; an na colour hides that bar's value.
    expect(byKey.p0).toMatchObject({ kind: 'line', pane: 1, lineWidth: 2, precision: 3 });
    expect(byKey.p0.points.map((point) => [point.value, point.color])).toEqual([[1, '#F23645'], [4, '#089981']]);
    expect(byKey.p0.points[0].time).toBe(bars(4)[0].start_time);
    expect(byKey.p1).toMatchObject({ kind: 'histogram', color: '#2962FF' });
    expect(byKey.p2).toMatchObject({ pane: 0, render: 'markers', marker: 'arrowUp', markerPosition: 'belowBar', markerText: 'X' });
    expect(byKey.p2.points).toHaveLength(1);
    expect(byKey.p3).toMatchObject({ kind: 'background', pane: 0 });
    expect(byKey.p3.points.map((point) => point.color)).toEqual(['rgba(253, 216, 53, 0.2)', '#FDD835']);
    expect(byKey.p4).toMatchObject({ kind: 'bar-colors' });
    expect(byKey.p5).toBeUndefined();
    expect(byKey.hline0).toMatchObject({ render: 'levels', lineStyle: 'dotted', pane: 1 });
    expect(byKey.hline0.points).toHaveLength(4);
    // x indexes the run's bars (run bar 1 is the chart's first); a line drawn right to left starts at its earlier point.
    expect(byKey.d1.points).toEqual([{ time: bars(4)[0].start_time, value: 100 }, { time: bars(4)[3].start_time, value: 110 }]);
    expect(byKey['d2:top'].points.map((point) => point.value)).toEqual([105, 105]);
    expect(byKey.labels.points).toEqual([{ time: bars(4)[3].start_time, value: 104, label: 'last', color: '#F23645' }]);
    // An empty plotcandle has nothing to draw; the fill and the table are drawn.
    expect(byKey.p6).toBeUndefined();
    expect(byKey.f0).toMatchObject({ kind: 'fill', pane: 1, title: 'Fill 1' });
    expect(byKey.t4).toMatchObject({ kind: 'table', pane: 1, points: [] }); // in the script's own pane
    expect(mapped.notDrawn).toEqual([]);
  });

  it('shades a fill between two plots or levels, with a gap where a value is na', () => {
    const mapped = scriptOutputs(instance, result({
      plots: [
        { index: 0, kind: 'plot', title: 'Up', options: {}, values: [5, 6, null, 8, 9], colors: null },
        { index: 1, kind: 'plot', title: 'Down', options: {}, values: [1, 2, 3, 4, 10], colors: null },
      ],
      hlines: [{ price: 7, title: 'Seven' }],
      fills: [{ from: 0, to: 1, color: { color: '#089981' }, title: 'Band' }, { from: ['hline', 0], to: 1 }],
    }), runTimes, bars(4));
    const byKey = Object.fromEntries(mapped.outputs.map((output) => [output.key.replace('script-sabc:', ''), output]));
    expect(byKey.f0).toMatchObject({ kind: 'fill', title: 'Band', color: '#089981', labelsOnPriceScale: false, valuesInStatusLine: false });
    // One point per chart bar: the na bar is a gap; the higher value is always the top, whichever plot it comes from.
    expect(byKey.f0.points.map((point) => [point.high, point.low])).toEqual([[6, 2], [Number.NaN, Number.NaN], [8, 4], [10, 9]]);
    expect(byKey.f1.points.map((point) => [point.high, point.low])).toEqual([[7, 2], [7, 3], [7, 4], [10, 7]]);
    // Only fill() shades: the chart's automatic band between an indicator's lines is off for scripts.
    expect(mapped.outputs.every((output) => output.backgroundVisible === false)).toBe(true);
  });

  it('draws plotcandle as candles and plotbar as OHLC bars, coloured per bar', () => {
    const ohlc = [[1, 3, 0, 2], [2, 4, 1, 3], null, [3, 5, 2, 4], [4, 6, 3, 5]];
    const mapped = scriptOutputs(instance, result({
      plots: [
        { index: 0, kind: 'plotcandle', title: 'C', options: {}, values: ohlc, colors: [null, { color: '#F23645' }, null, null, { color: '#089981' }] },
        { index: 1, kind: 'plotbar', title: 'B', options: { color: '#2962FF' }, values: ohlc, colors: null },
      ],
    }), runTimes, bars(4));
    const byKey = Object.fromEntries(mapped.outputs.map((output) => [output.key.replace('script-sabc:', ''), output]));
    expect(byKey.p0).toMatchObject({ kind: 'candles', pane: 1 });
    expect(byKey.p0.barStyle).toBeUndefined();
    expect(byKey.p0.points.map((point) => [point.open, point.high, point.low, point.value, point.color])).toEqual([
      [2, 4, 1, 3, '#F23645'], [3, 5, 2, 4, undefined], [4, 6, 3, 5, '#089981'],
    ]);
    expect(byKey.p1).toMatchObject({ kind: 'candles', barStyle: 'bars' });
    expect(byKey.p1.points.every((point) => point.color === '#2962FF')).toBe(true);
  });

  it('reads a table, its cells by column and row, and its position', () => {
    const table = scriptTable({
      position: 'position.bottom_left', columns: 2, rows: 2, bgcolor: { color: '#131722' }, border_color: { color: '#FFFFFF' },
      cells: { '(0, 0)': { text: 'RSI', text_color: { color: '#D1D4DC' } }, '(1,1)': { text: 42, bgcolor: { color: '#089981' } }, '(5, 0)': { text: 'off' }, junk: { text: 'x' } },
    });
    expect(table).toEqual({
      position: 'bottom_left', background: '#131722', border: '#FFFFFF',
      rows: [[{ text: 'RSI', color: '#D1D4DC' }, null], [null, { text: '42', background: '#089981' }]],
    });
    expect(scriptTable({ position: 'nowhere', columns: 1, rows: 1 })).toEqual({ position: 'top_right', rows: [[null]] });
  });

  it('runs the saved script on the server once per bars and reports errors to the console', async () => {
    tradingApi.document.mockResolvedValue({ record_id: 'sabc', revision: 2, payload: { name: 'Mine', source: 'plot(close)' } });
    scriptsApi.run.mockResolvedValue({ times: runTimes, error: null, result: result({ plots: [{ index: 0, kind: 'plot', title: 'C', options: {}, values: [1, 2, 3, 4, 5], colors: null }] }) });
    const chartBars = bars(4);
    const withInput = { ...instance, params: withScriptInput(instance.params, 'Length', 5) } as CoreIndicatorInstance;
    const [output] = await calculateScriptIndicatorOutputs(chartBars, withInput, { bindingId: 'b1' });
    expect(output.points).toHaveLength(4);
    expect(scriptsApi.run).toHaveBeenCalledWith({ source: 'plot(close)', instrumentId: 'equity:NASDAQ:AAPL', bindingId: 'b1', interval: '1m', inputs: { Length: 5 }, limit: 4 });
    await calculateScriptIndicatorOutputs(chartBars, withInput, { bindingId: 'b1' });
    expect(scriptsApi.run).toHaveBeenCalledTimes(1);
    expect(tradingApi.document).toHaveBeenCalledTimes(1);

    scriptsApi.run.mockResolvedValue({ times: [], result: null, error: { kind: 'runtime', message: 'division by zero', line: 3, column: 0 } });
    expect(await calculateScriptIndicatorOutputs(bars(5), withInput)).toEqual([]);
    expect(scriptRunStatus('sabc')?.error).toBe('line 3: division by zero');
  });
});

describe('script runs on a live chart', () => {
  it('re-runs a forming bar at most every few seconds, and a new bar at once', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    try {
      vi.clearAllMocks();
      vi.setSystemTime(Date.parse('2026-10-09T12:00:00Z'));
      forgetScriptSource('slive');
      tradingApi.document.mockResolvedValue({ record_id: 'slive', revision: 1, payload: { name: 'Live', source: 'plot(close)' } });
      scriptsApi.run.mockResolvedValue({ times: runTimes, error: null, result: result() });
      const live = scriptIndicatorInstance('slive', 'Live', true, 1);
      const chart = bars(4);
      const tick = (close: string) => chart.map((bar, index) => (index === 3 ? { ...bar, close, is_final: false } : bar));
      await calculateScriptIndicatorOutputs(tick('103'), live);
      await calculateScriptIndicatorOutputs(tick('104'), live);
      expect(scriptsApi.run).toHaveBeenCalledTimes(1);
      vi.setSystemTime(Date.parse('2026-10-09T12:00:06Z'));
      await calculateScriptIndicatorOutputs(tick('105'), live);
      expect(scriptsApi.run).toHaveBeenCalledTimes(2);
      await calculateScriptIndicatorOutputs([...chart, ...bars(5).slice(4)], live);
      expect(scriptsApi.run).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('alerts on script indicators (TVP-11.4)', () => {
  it('offers the plots, alertcondition() and alert() calls of the run, and alerts on the revision that ran', async () => {
    vi.clearAllMocks();
    forgetScriptSource('salert');
    const { alertIndicatorChoices, withChartIndicatorCondition } = await import('../alertIndicatorSources');
    const { alertConditionsSummary } = await import('../tradingChartAlerts');
    tradingApi.document.mockResolvedValue({ record_id: 'salert', revision: 4, payload: { name: 'Breakout', source: 'plot(close)\nalertcondition(close > 1, "Up")\nif close > 2\n    alert("x")' } });
    scriptsApi.run.mockResolvedValue({
      times: runTimes, error: null,
      result: result({ plots: [
        { index: 0, kind: 'plot', title: 'Close', options: {}, values: [1, 2, 3, 4, 5], colors: null },
        { index: 1, kind: 'alertcondition', title: 'Up', options: { message: 'up' }, values: [false, true, true, true, true], colors: null },
      ] }),
    });
    const instance = { ...scriptIndicatorInstance('salert', 'Breakout', false, 3), params: withScriptInput(scriptIndicatorInstance('salert', 'Breakout', false, 3).params, 'Level', 2) };
    const outputs = await calculateScriptIndicatorOutputs(bars(4), instance);
    const [choice] = alertIndicatorChoices([instance], outputs, new Set());
    expect(choice.unavailable).toBeUndefined();
    expect(choice.outputs).toEqual([
      { key: 'script-salert:p0', title: 'Close' },
      { key: 'script-salert:ac1', title: 'Up', signal: true },
      { key: 'script-salert:alert', title: 'Any alert() function call', signal: true },
    ]);
    const input = { condition_type: 'price_above', threshold: '0', parameters: {} } as never as Parameters<typeof withChartIndicatorCondition>[0];
    expect(withChartIndicatorCondition(input, [choice], { key: choice.key, output: 'script-salert:ac1', operator: 'appears' }, '0')).toBe(true);
    expect(input.conditions?.[0].source).toEqual({ kind: 'script', script_id: 'salert', revision: 4, inputs: { Level: 2 }, output: 'alertcondition:1' });
    expect(alertConditionsSummary(input as never)).toBe('Script alertcondition() fires');
    withChartIndicatorCondition(input, [choice], { key: choice.key, output: 'script-salert:p0', operator: 'crossing_up' }, '3');
    expect(input.conditions?.[0]).toMatchObject({ source: { output: 'plot:0' }, operator: 'crossing_up', target: { kind: 'value', value: '3' } });
    expect(alertConditionsSummary(input as never)).toContain('Script plot 1');
  });
});
