import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { newConditionDraft } from './alertConditionDrafts';
import { indicatorConditionSpec, type AlertIndicatorChoice } from './alertIndicatorSources';
import { lineTargetOptions } from './AlertLineField';
import { TradingAlertDialog, type TradingAlertEditorState } from './TradingAlertDialog';

afterEach(cleanup);

const inputs = { period: 50 } as AlertIndicatorChoice['inputs'];
const sma: AlertIndicatorChoice = { key: 'sma', label: 'SMA 50', outputs: [{ key: 'sma:50', title: 'SMA' }], inputs };
const rsi: AlertIndicatorChoice = { key: 'rsi', label: 'RSI 14', outputs: [{ key: 'rsi:14', title: 'RSI' }], inputs: { period: 14 } as AlertIndicatorChoice['inputs'] };
const choices = [
  sma,
  rsi,
  { key: 'tv-fractal', label: 'Fractal', outputs: [{ key: 'tv-fractal:up', title: 'Up', signal: true as const }], inputs },
  { key: 'tv-pivots', label: 'Pivots', outputs: [{ key: 'tv-pivots:p', title: 'P' }], inputs, unavailable: 'Not on the server' },
];
const smaLine = { kind: 'indicator', indicatorId: 'sma', indicatorInputs: inputs, output: 'sma:50' };

const editor = (overrides: Partial<TradingAlertEditorState> = {}): TradingAlertEditorState => ({
  mode: 'create', alertId: null, x: 0, y: 0, condition: 'price_above', threshold: '100', expiresAt: '', expiration: 'never',
  triggerPolicy: 'once', message: '', notifications: ['app'], indicator: 'rsi', period: '14', lookback: '1', ...overrides,
});

const dialog = (state: TradingAlertEditorState, onChange = vi.fn()) => render(
  <TradingAlertDialog editor={state} symbol="AAPL" latestPrice={101} status="idle" onChange={onChange} onSubmit={vi.fn()} onClose={vi.fn()} indicatorChoices={choices} />,
);

describe('another line as an alert target (TVP-1.3)', () => {
  it('offers the price fields and the server-evaluated lines, not signals or unavailable indicators', () => {
    const labels = lineTargetOptions(choices).map((option) => option.label);
    expect(labels).toEqual(['Close', 'Open', 'High', 'Low', 'HL2', 'HLC3', 'OHLC4', 'Volume', 'SMA 50: SMA', 'RSI 14: RSI']);
  });

  it('lets a price alert cross a chart indicator line instead of a value', () => {
    const onChange = vi.fn();
    dialog(editor(), onChange);
    fireEvent.change(screen.getByLabelText('Alert price target'), { target: { value: within(screen.getByLabelText('Alert price target')).getByText('SMA 50: SMA').getAttribute('value') } });
    expect(onChange).toHaveBeenCalledWith({ priceLine: smaLine });
    cleanup();
    dialog(editor({ priceLine: smaLine as TradingAlertEditorState['priceLine'] }));
    expect((screen.getByLabelText('Alert price target') as HTMLSelectElement).selectedOptions[0].textContent).toBe('SMA 50: SMA');
    expect(screen.queryByLabelText('Alert value')).toBeNull();
  });

  it("sets an added condition's target to a line, hiding its value", () => {
    const onChange = vi.fn();
    dialog(editor({ conditionDrafts: [newConditionDraft('5')] }), onChange);
    fireEvent.change(screen.getByLabelText('Condition 2 value target'), { target: { value: 'price:high' } });
    expect(onChange).toHaveBeenCalledWith({ conditionDrafts: [expect.objectContaining({ targetLine: { kind: 'price', field: 'high' } })], conditionError: undefined });
    cleanup();
    dialog(editor({ conditionDrafts: [{ ...newConditionDraft(''), operator: 'entering_channel', upperLine: { kind: 'price', field: 'high' }, lower: '90' }] }));
    expect(screen.queryByLabelText('Condition 2 upper')).toBeNull();
    expect((screen.getByLabelText('Condition 2 lower') as HTMLInputElement).value).toBe('90');
  });

  it('shows a stored line the chart no longer has as saved', () => {
    dialog(editor({ conditionDrafts: [{ ...newConditionDraft(''), targetLine: { kind: 'indicator', indicatorId: 'ema', indicatorInputs: { period: 9 } as never, output: 'ema:9' } }] }));
    expect((screen.getByLabelText('Condition 2 value target') as HTMLSelectElement).selectedOptions[0].textContent).toBe('ema: ema:9 (as saved)');
  });

  it("compares a chart indicator against another line, and the condition carries it", () => {
    const onChange = vi.fn();
    const selection = { key: 'rsi', output: 'rsi:14', operator: 'crossing_up' as const };
    dialog(editor({ condition: 'indicator_above', indicatorSelection: selection }), onChange);
    fireEvent.change(screen.getByLabelText('Alert indicator target'), { target: { value: within(screen.getByLabelText('Alert indicator target')).getByText('SMA 50: SMA').getAttribute('value') } });
    expect(onChange).toHaveBeenCalledWith({ indicatorSelection: { ...selection, target: smaLine } });
    cleanup();
    dialog(editor({ condition: 'indicator_above', indicatorSelection: { ...selection, target: smaLine as never } }));
    expect(screen.queryByLabelText('Alert value')).toBeNull();
    expect(indicatorConditionSpec(rsi, { ...selection, target: smaLine as never }, '70')).toEqual({
      source: { kind: 'indicator', indicator_id: 'rsi', inputs: { period: 14 }, output: 'rsi:14' },
      operator: 'crossing_up',
      target: { kind: 'source', source: { kind: 'indicator', indicator_id: 'sma', inputs, output: 'sma:50' } },
    });
  });
});
