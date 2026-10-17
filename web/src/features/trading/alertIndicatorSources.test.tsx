import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AlertIndicatorPicker } from './AlertIndicatorPicker';
import { alertIndicatorChoices, APPEARS_VALUE, chartAlertIndicatorId, chartAlertThreshold, conditionsAtValue, defaultIndicatorSelection, resolveIndicatorSelection, withChartIndicatorCondition } from './alertIndicatorSources';
import { editorDefaults } from './TradingChartAlertOverlay';
import type { CoreIndicatorInstance, IndicatorOutput } from './indicators/coreIndicators';
import { alertConditionsSummary, chartAlertCreateInput } from './tradingChartAlerts';

afterEach(cleanup);

const line = (key: string, title: string) => ({ key, title, pane: 1, kind: 'line', points: [] }) as unknown as IndicatorOutput;
const instances: CoreIndicatorInstance[] = [
  { id: 'macd', period: 9, fastPeriod: 12, slowPeriod: 26, signalPeriod: 9, enabled: true },
  { id: 'rsi', period: 14, enabled: false },
  { id: 'vwap', period: 1, enabled: true },
  { id: 'tv-correlation-coefficient-cc', period: 20, enabled: true, compareSymbol: 'equity:NASDAQ:QQQ' } as unknown as CoreIndicatorInstance,
];
const outputs = [line('macd:12:26:line', 'MACD'), line('macd:12:26:signal', 'Signal'), line('vwap:dataset', 'Anchored VWAP'), line('tv-correlation-coefficient-cc:value', 'CC')];

describe('alerts on chart indicators (TVP-1.3)', () => {
  it('offers the chart indicators with their lines, greying out what the server cannot evaluate', () => {
    const choices = alertIndicatorChoices(instances, outputs, new Set(['macd', 'tv-correlation-coefficient-cc']));
    expect(choices.map((choice) => [choice.key, choice.outputs.map((output) => output.key), choice.unavailable ?? null])).toEqual([
      ['macd', ['macd:12:26:line', 'macd:12:26:signal'], null],
      ['vwap', ['vwap:dataset'], 'Server alerts are not available for this indicator yet'],
      // Its compare symbol goes with the alert; the server loads its bars (TVP-1.3).
      ['tv-correlation-coefficient-cc', ['tv-correlation-coefficient-cc:value'], null],
    ]);
    expect(choices[0].inputs).toMatchObject({ period: 9, fast_period: 12, slow_period: 26, signal_period: 9 });
    expect(choices[2].inputs).toMatchObject({ period: 20, compare_symbol: 'equity:NASDAQ:QQQ', params: {} });
    const withoutCompare = alertIndicatorChoices([{ id: 'tv-correlation-coefficient-cc', period: 20, enabled: true } as never], [line('tv-correlation-coefficient-cc:value', 'CC')], new Set(['tv-correlation-coefficient-cc']));
    expect(withoutCompare[0].unavailable).toMatch(/compare symbol/);
    expect(defaultIndicatorSelection(choices)).toEqual({ key: 'macd', output: 'macd:12:26:line', operator: 'crossing' });
  });

  it('creates a conditions alert on the chosen line', () => {
    const choices = alertIndicatorChoices(instances, outputs, new Set(['macd']));
    const input = chartAlertCreateInput({ alertId: 'a', instrumentId: 'btc', bindingId: null, interval: '1h', threshold: 0, latestPrice: 1, expiration: 'never' });
    expect(withChartIndicatorCondition(input, choices, { key: 'macd', output: 'macd:12:26:signal', operator: 'crossing_up' }, '0.5')).toBe(true);
    expect(input.condition_type).toBe('conditions');
    expect(input.conditions).toEqual([{
      source: { kind: 'indicator', indicator_id: 'macd', inputs: expect.objectContaining({ period: 9, fast_period: 12 }), output: 'macd:12:26:signal' },
      operator: 'crossing_up',
      target: { kind: 'value', value: '0.5' },
    }]);
    // A greyed-out or unknown choice changes nothing.
    const other = chartAlertCreateInput({ alertId: 'b', instrumentId: 'btc', bindingId: null, interval: '1h', threshold: 0, latestPrice: 1, expiration: 'never' });
    expect(withChartIndicatorCondition(other, choices, { key: 'vwap', output: 'vwap:dataset', operator: 'crossing' }, '1')).toBe(false);
    expect(other.conditions).toBeUndefined();
  });

  it('picks the indicator, line and comparison', () => {
    const choices = alertIndicatorChoices(instances, outputs, new Set(['macd']));
    const onChange = vi.fn();
    render(<AlertIndicatorPicker choices={choices} selection={defaultIndicatorSelection(choices)} onChange={onChange} />);
    expect((screen.getByRole('option', { name: /VWAP.*not available/ }) as HTMLOptionElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Alert indicator line'), { target: { value: 'macd:12:26:signal' } });
    expect(onChange).toHaveBeenLastCalledWith({ key: 'macd', output: 'macd:12:26:signal', operator: 'crossing' });
    fireEvent.change(screen.getByLabelText('Alert indicator comparison'), { target: { value: 'less_than' } });
    expect(onChange).toHaveBeenLastCalledWith({ key: 'macd', output: 'macd:12:26:line', operator: 'less_than' });
  });
});

describe('alerts on signal outputs (TVP-6.3)', () => {
  const marker = (key: string, title: string) => ({ ...line(key, title), pane: 0, render: 'markers' }) as unknown as IndicatorOutput;
  const patterns = alertIndicatorChoices(
    [{ id: 'tv-all-candlestick-patterns', period: 1, enabled: true } as unknown as CoreIndicatorInstance, { id: 'sma', period: 20, enabled: true }],
    [marker('tv-all-candlestick-patterns:hammer', 'Hammer - Bullish'), marker('tv-all-candlestick-patterns:doji', 'Doji'), line('sma:20', 'SMA')],
    new Set(['tv-all-candlestick-patterns', 'sma']),
  );

  it('offers "appears" on a pattern, stored as the output above 0 whatever the value field says', () => {
    expect(patterns[0].outputs[0]).toEqual({ key: 'tv-all-candlestick-patterns:hammer', title: 'Hammer - Bullish', signal: true });
    const selection = defaultIndicatorSelection(patterns)!;
    expect(selection).toEqual({ key: 'tv-all-candlestick-patterns', output: 'tv-all-candlestick-patterns:hammer', operator: 'appears' });
    const input = chartAlertCreateInput({ alertId: 'a', instrumentId: 'btc', bindingId: null, interval: '1h', threshold: 0, latestPrice: 1, expiration: 'never' });
    expect(withChartIndicatorCondition(input, patterns, selection, '187.5')).toBe(true);
    expect(input.conditions?.[0]).toMatchObject({ operator: 'greater_than', target: { kind: 'value', value: APPEARS_VALUE }, source: { output: 'tv-all-candlestick-patterns:hammer' } });
    // No line to draw or drag on the chart, and it reads as "appears".
    const alert = { condition_type: 'conditions', threshold: 0, parameters: {}, conditions: input.conditions };
    expect(chartAlertIndicatorId(alert)).toBeNull();
    expect(conditionsAtValue(alert, '187.5')).toBe(input.conditions);
    expect(alertConditionsSummary({ conditions: input.conditions as never })).toBe('tv-all-candlestick-patterns:hammer appears');
    // Back on a line, the comparison is a line's again.
    expect(resolveIndicatorSelection(patterns, { ...selection, key: 'sma' })).toEqual({ key: 'sma', output: 'sma:20', operator: 'crossing' });
  });

  it('greys out fractals: they are confirmed bars after the bar alerts evaluate', () => {
    const fractals = alertIndicatorChoices(
      [{ id: 'tv-williams-fractal', period: 2, enabled: true } as unknown as CoreIndicatorInstance],
      [marker('tv-williams-fractal:up-fractal', 'Up Fractal')],
      new Set(['tv-williams-fractal']),
    );
    expect(fractals[0].unavailable).toMatch(/confirmed bars later/);
  });

  it('shows only "Appears" for a pattern and switches the comparison with the indicator', () => {
    const onChange = vi.fn();
    render(<AlertIndicatorPicker choices={patterns} selection={defaultIndicatorSelection(patterns)} onChange={onChange} />);
    expect(Array.from((screen.getByLabelText('Alert indicator comparison') as HTMLSelectElement).options).map((option) => option.text)).toEqual(['Appears']);
    fireEvent.change(screen.getByLabelText('Alert chart indicator'), { target: { value: 'sma' } });
    expect(onChange).toHaveBeenLastCalledWith({ key: 'sma', output: 'sma:20', operator: 'crossing' });
  });
});

describe('chart indicator alerts stay what the dialog showed (TVP-1.3 review)', () => {
  const choices = alertIndicatorChoices(
    [{ id: 'sma', period: 20, enabled: true }, { id: 'rsi', period: 14, enabled: true }],
    [line('sma:20', 'SMA'), line('rsi:14', 'RSI')],
    new Set(['sma', 'rsi']),
  );

  it('starts on the pane the alert was placed on, and repairs a selection that went stale', () => {
    expect(resolveIndicatorSelection(choices, undefined, 'rsi')).toEqual({ key: 'rsi', output: 'rsi:14', operator: 'crossing' });
    expect(resolveIndicatorSelection(choices, { key: 'macd', output: 'macd:12:26:line', operator: 'less_than' }, 'rsi')?.key).toBe('rsi');
    expect(resolveIndicatorSelection(choices, { key: 'sma', output: 'sma:50', operator: 'less_than' })).toEqual({ key: 'sma', output: 'sma:20', operator: 'less_than' });
    expect(editorDefaults({ time: 't', price: 70, x: 0, y: 0, source: 'context-menu', chartIndicatorId: 'tv-awesome-oscillator-ao' }, 100))
      .toMatchObject({ condition: 'indicator_above', chartIndicatorId: 'tv-awesome-oscillator-ao' });
  });

  it('offers nothing while the server list loads; session indicators and params go to the server', () => {
    expect(alertIndicatorChoices([{ id: 'sma', period: 20, enabled: true }], [line('sma:20', 'SMA')], null)[0].unavailable).toMatch(/Checking/);
    // The server reads the instrument's session hours itself, and takes the indicator's params.
    const session = alertIndicatorChoices([{ id: 'tv-relative-volume-at-time', period: 20, enabled: true, params: { anchor: 'W', flag: true } } as never], [line('tv-relative-volume-at-time:value', 'RVOL')], new Set(['tv-relative-volume-at-time']));
    expect(session[0].unavailable).toBeUndefined();
    expect(session[0].inputs.params).toEqual({ anchor: 'W' });
  });

  it('draws and drags a single indicator condition like a legacy indicator alert, without rounding its value', () => {
    const alert = {
      condition_type: 'conditions', threshold: '0', parameters: {},
      conditions: [{ source: { kind: 'indicator', indicator_id: 'macd', output: 'macd:12:26:line' }, operator: 'crossing', target: { kind: 'value', value: '0.005' } }],
    };
    expect(chartAlertIndicatorId(alert)).toBe('macd');
    expect(chartAlertThreshold(alert)).toBe(0.005);
    expect(conditionsAtValue(alert, '0.0042')).toEqual([{ ...alert.conditions[0], target: { kind: 'value', value: '0.0042' } }]);
    expect(chartAlertIndicatorId({ condition_type: 'indicator_above', threshold: '70', parameters: { indicator_id: 'rsi' } })).toBe('rsi');
    expect(chartAlertIndicatorId({ condition_type: 'price_above', threshold: '1', parameters: {} })).toBeNull();
  });
});

describe('pane alerts on indicators the server cannot alert on (TVP-1.3 review 2)', () => {
  it('asks for a choice instead of standing in another indicator, and keeps the pane value precise', () => {
    const choices = alertIndicatorChoices(
      [{ id: 'sma', period: 20, enabled: true }, { id: 'golden-cross', period: 50, enabled: true }],
      [line('sma:20', 'SMA'), line('golden-cross:50', 'GC')],
      new Set(['sma']),
    );
    expect(defaultIndicatorSelection(choices, 'golden-cross')).toBeUndefined();
    expect(defaultIndicatorSelection(choices)?.key).toBe('sma');
    expect(editorDefaults({ time: 't', price: 0.00423456789, x: 0, y: 0, source: 'context-menu', chartIndicatorId: 'macd' }, 1).threshold).toBe('0.0042345679');
  });
});

describe('external-data indicators as alert sources (TVP-0.2)', () => {
  it('offers them when the server evaluates them, but not an indicator computed on one', () => {
    const oi = { id: 'tv-open-interest', period: 14, enabled: true } as unknown as CoreIndicatorInstance;
    const smaOfOi = { id: 'sma', period: 5, enabled: true, source: { indicatorId: 'tv-open-interest', output: 'open-interest' } } as unknown as CoreIndicatorInstance;
    const choices = alertIndicatorChoices([oi, smaOfOi], [line('tv-open-interest:open-interest', 'Open Interest'), line('sma:5', 'SMA')], new Set(['tv-open-interest', 'sma']));
    expect(choices[0]).toMatchObject({ key: 'tv-open-interest', outputs: [{ key: 'tv-open-interest:open-interest' }] });
    expect(choices[0].unavailable).toBeUndefined();
    expect(choices[1].unavailable).toMatch(/source indicator/);
  });
});
