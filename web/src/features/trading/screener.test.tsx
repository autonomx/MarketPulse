import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { onOmnixEvent } from '../../events/bus';
import { ScreenerResultsTable } from './ScreenerResultsTable';
import { ScreenerRuleEditor } from './ScreenerRuleEditor';
import { newScreenerRule, screenerHistoryNeeded, screenerRuleLabel, screenerValue, sortScreenerResults, type ScreenerRuleInput } from './screenerRules';
import type { TradingScannerResult, TradingScannerRule } from './scannerTypes';
import { requestWatchlistAdd, useWatchlistAddRequests } from './tradingWatchlistEvents';
import { renderHook } from '@testing-library/react';

afterEach(cleanup);

const rule = (overrides: Partial<ScreenerRuleInput>): TradingScannerRule => ({ ...newScreenerRule(), ...overrides }) as TradingScannerRule;
const result = (instrumentId: string, metrics: Record<string, string>, rank = 1, score = '1') => ({
  run_id: 'run', instrument_id: instrumentId, rank, score, metrics, provider: 'p', dataset_fingerprint: 'f',
}) as unknown as TradingScannerResult;

describe('screener rules (TVP-9.1)', () => {
  it('reads values by rule id, else by the legacy metric key', () => {
    const change = rule({ rule_id: 'c', metric: 'percent_change', lookback_bars: 5 });
    expect(screenerValue(result('a', { 'rule:c': '2.5' }), change)).toBe(2.5);
    expect(screenerValue(result('a', { 'percent_change:5': '1.5' }), change)).toBe(1.5);
    expect(screenerValue(result('a', {}), change)).toBeNull();
    expect(screenerRuleLabel(rule({ metric: 'relative_volume', period: 20 }))).toBe('relative volume 20');
    expect(screenerRuleLabel(rule({ metric: 'indicator', source: { kind: 'indicator', indicator_id: 'rsi', inputs: { period: 14 }, output: 'rsi:14' } as never }))).toBe('rsi:14');
    expect(screenerHistoryNeeded([rule({ metric: 'high_distance_percent', period: 252 }) as ScreenerRuleInput])).toBe(253);
  });

  it('sorts by any column, missing values last', () => {
    const rvol = rule({ rule_id: 'r', metric: 'relative_volume', period: 20 });
    const rows = [result('a', { 'rule:r': '1.2' }, 1), result('b', {}, 2), result('c', { 'rule:r': '3' }, 3)];
    expect(sortScreenerResults(rows, [rvol], { key: 'rule:r', direction: 'desc' }).map((row) => row.instrument_id)).toEqual(['c', 'a', 'b']);
    expect(sortScreenerResults(rows, [rvol], { key: 'rule:r', direction: 'asc' }).map((row) => row.instrument_id)).toEqual(['a', 'c', 'b']);
    expect(sortScreenerResults(rows, [rvol], { key: 'symbol', direction: 'desc' }).map((row) => row.instrument_id)).toEqual(['c', 'b', 'a']);
  });
});

describe('screener editor and results (TVP-9.1)', () => {
  it('adds filters and columns, and offers an indicator with its lines', () => {
    const onChange = vi.fn();
    const first = newScreenerRule('filter');
    const view = render(<ScreenerRuleEditor rules={[first]} indicatorIds={['rsi', 'macd']} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Add column' }));
    expect(onChange.mock.calls[0][0][1]).toMatchObject({ role: 'column' });
    fireEvent.change(screen.getByLabelText(`Metric of ${first.rule_id}`), { target: { value: 'indicator' } });
    const indicatorRule = onChange.mock.calls[1][0][0] as ScreenerRuleInput;
    expect(indicatorRule.source).toMatchObject({ kind: 'indicator', indicator_id: 'rsi', output: 'rsi:14' });
    view.rerender(<ScreenerRuleEditor rules={[{ ...indicatorRule }]} indicatorIds={['rsi', 'macd']} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText(`Indicator of ${first.rule_id}`), { target: { value: 'macd' } });
    expect((onChange.mock.calls[2][0][0] as ScreenerRuleInput).source).toMatchObject({ indicator_id: 'macd', output: expect.stringMatching(/^macd:/) });
    expect(screen.getByRole('button', { name: 'Remove rule 1' })).toBeDisabled();
  });

  it('sorts results by a header, shows a symbol on the chart, and adds the results to the watchlist', () => {
    const rvol = rule({ rule_id: 'r', metric: 'relative_volume', period: 20 });
    const onShow = vi.fn();
    const added: string[][] = [];
    const stop = onOmnixEvent('omnix:trading-watchlist-add', ({ instrumentIds }) => added.push(instrumentIds));
    render(<ScreenerResultsTable results={[result('a', { 'rule:r': '1' }, 1), result('c', { 'rule:r': '3' }, 2)]} rules={[rvol]} added={new Set(['c'])} symbolOf={(id) => id.toUpperCase()} onShow={onShow} />);
    fireEvent.click(screen.getByRole('button', { name: 'relative volume 20' }));
    const rows = screen.getAllByRole('row').slice(1);
    expect(within(rows[0]).getByRole('button', { name: 'C' })).toBeInTheDocument();
    expect(rows[0]).toHaveClass('is-new');
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'C' }));
    expect(onShow).toHaveBeenCalledWith('c');
    fireEvent.click(screen.getByRole('button', { name: 'Add 2 to the watchlist' }));
    expect(added).toEqual([['c', 'a']]);
    stop();
  });
});

describe('watchlist add requests (TVP-9.1)', () => {
  it('reach the open, editable watchlist and say whether one took them', () => {
    expect(requestWatchlistAdd(['a'])).toBe(false);
    const add = vi.fn();
    const hook = renderHook(({ enabled }) => useWatchlistAddRequests(add, enabled), { initialProps: { enabled: true } });
    expect(requestWatchlistAdd(['a', 'b'])).toBe(true);
    expect(add).toHaveBeenCalledWith(['a', 'b']);
    hook.rerender({ enabled: false });
    expect(requestWatchlistAdd(['c'])).toBe(false);
    hook.unmount();
  });
});

describe('external-data indicators in the screener (TVP-0.2)', () => {
  it('offers their data series as lines, without a period', () => {
    const onChange = vi.fn();
    const rule = { ...newScreenerRule('filter'), metric: 'indicator', source: { kind: 'indicator', indicator_id: 'tv-advance-decline-line', inputs: { period: 14 }, output: 'tv-advance-decline-line:nyse' } } as ScreenerRuleInput;
    render(<ScreenerRuleEditor rules={[rule]} indicatorIds={['rsi', 'tv-advance-decline-line']} onChange={onChange} />);
    const lines = within(screen.getByLabelText(`Indicator line of ${rule.rule_id}`)).getAllByRole('option').map((option) => option.getAttribute('value'));
    expect(lines).toEqual(['tv-advance-decline-line:nyse', 'tv-advance-decline-line:nasdaq']);
    expect(screen.queryByLabelText(`Indicator period of ${rule.rule_id}`)).toBeNull();
  });
});
