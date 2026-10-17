import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { newConditionDraft } from './alertConditionDrafts';
import { TradingAlertDialog, type TradingAlertEditorState } from './TradingAlertDialog';

afterEach(cleanup);

const editor = (overrides: Partial<TradingAlertEditorState> = {}): TradingAlertEditorState => ({
  mode: 'create', alertId: null, x: 0, y: 0, condition: 'price_above', threshold: '100', expiresAt: '', expiration: 'never',
  triggerPolicy: 'once', message: '', notifications: ['app'], indicator: 'rsi', period: '14', lookback: '1', ...overrides,
});

const dialog = (state: TradingAlertEditorState, onChange = vi.fn()) => render(
  <TradingAlertDialog editor={state} symbol="BTCUSDT" latestPrice={101} status="idle" onChange={onChange} onSubmit={vi.fn()} onClose={vi.fn()} />,
);

describe('alert dialog conditions (TVP-1.6)', () => {
  it("adds conditions after a price alert's own, up to five in all", () => {
    const onChange = vi.fn();
    dialog(editor(), onChange);
    fireEvent.click(screen.getByRole('button', { name: /Add condition/ }));
    expect(onChange).toHaveBeenCalledWith({ conditionDrafts: [expect.objectContaining({ source: 'close', value: '101' })] });
    cleanup();
    const four = Array.from({ length: 4 }, () => newConditionDraft('1'));
    const full = vi.fn();
    dialog(editor({ conditionDrafts: four }), full);
    expect(screen.getByLabelText('Condition 5 source')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Add condition/ }));
    expect(full).not.toHaveBeenCalled();
    expect(screen.getByRole('status').textContent).toMatch(/at most 5/);
  });

  it('keeps drawing alerts to one condition, saying why', () => {
    const onChange = vi.fn();
    dialog(editor({ condition: 'trendline_crossing', trendlinePoints: [{ time: 'a', price: 1 }, { time: 'b', price: 2 }] }), onChange);
    fireEvent.click(screen.getByRole('button', { name: /Add condition/ }));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole('status').textContent).toMatch(/follow their drawing/);
  });
});

describe('alert dialog conditions review fixes (TVP-1.6)', () => {
  it('shows why the conditions can\'t be saved, and drops added ones for a family that takes one', () => {
    const onChange = vi.fn();
    dialog(editor({ conditionDrafts: [newConditionDraft('1')], conditionError: 'Each condition needs a value.' }), onChange);
    expect(screen.getByRole('alert').textContent).toBe('Each condition needs a value.');
    fireEvent.change(screen.getByRole('combobox', { name: 'Alert condition' }), { target: { value: 'indicator_above' } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ condition: 'indicator_above', conditionDrafts: [] }));
  });

  it('shows a stored indicator as saved when the chart has it with other inputs, and follows the chart\'s when it is the same', () => {
    const stored = { ...newConditionDraft('70'), source: 'indicator' as const, indicatorId: 'rsi', indicatorInputs: { period: 14 }, output: 'rsi:14', operator: 'greater_than' as const };
    const rsi = (period: number) => ({ key: 'rsi', label: `RSI ${period}`, inputs: { period }, outputs: [{ key: `rsi:${period}`, title: `RSI ${period}` }] });
    const onChange = vi.fn();
    render(<TradingAlertDialog editor={editor({ conditionDrafts: [stored] })} symbol="BTCUSDT" latestPrice={101} status="idle" onChange={onChange} onSubmit={vi.fn()} onClose={vi.fn()} indicatorChoices={[rsi(21)] as never} />);
    const indicator = screen.getByLabelText('Condition 2 indicator') as HTMLSelectElement;
    expect(indicator.selectedOptions[0].textContent).toBe('rsi (as saved)');
    expect((screen.getByLabelText('Condition 2 line') as HTMLSelectElement).value).toBe('rsi:14');
    cleanup();
    render(<TradingAlertDialog editor={editor({ conditionDrafts: [stored] })} symbol="BTCUSDT" latestPrice={101} status="idle" onChange={onChange} onSubmit={vi.fn()} onClose={vi.fn()} indicatorChoices={[rsi(14)] as never} />);
    expect((screen.getByLabelText('Condition 2 indicator') as HTMLSelectElement).selectedOptions[0].textContent).toBe('RSI 14');
  });
});
