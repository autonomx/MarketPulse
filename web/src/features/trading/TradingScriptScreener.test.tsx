import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { filterHolds, screenMatches, type ScreenFilter } from './scripts/scriptScreen';

const scriptsApi = vi.hoisted(() => ({ screen: vi.fn() }));
vi.mock('./scripts/scriptsApi', () => ({ scriptsApi }));
const tradingApi = vi.hoisted(() => ({ allDocuments: vi.fn(), documents: vi.fn() }));
vi.mock('./tradingApi', () => ({ tradingApi }));

const { TradingScriptScreener } = await import('./TradingScriptScreener');

const row = (instrument_id: string, last: Record<string, number | null>, previous: Record<string, number | null> = {}, error: string | null = null) => ({ instrument_id, bar_time: '2026-10-09T00:00:00Z', last, previous, error });
const filter = (operator: ScreenFilter['operator'], value = 0, output = 'plot:0'): ScreenFilter => ({ id: operator, output, operator, value });

describe('script screen filters (TVP-11.6)', () => {
  it('checks values, crossings and conditions at the last bar', () => {
    const up = row('a', { 'plot:0': 9, 'alertcondition:1': 1 }, { 'plot:0': 6.5, 'alertcondition:1': 0 });
    expect(filterHolds(up, filter('above', 8))).toBe(true);
    expect(filterHolds(up, filter('below', 8))).toBe(false);
    expect(filterHolds(up, filter('crosses-above', 8))).toBe(true);
    expect(filterHolds(up, filter('crosses-above', 5))).toBe(false); // already above on the bar before
    expect(filterHolds(up, filter('crosses-below', 8))).toBe(false);
    expect(filterHolds(up, filter('true', 0, 'alertcondition:1'))).toBe(true);
    expect(filterHolds(row('b', { 'plot:0': null }), filter('below', 100))).toBe(false);
    expect(screenMatches([up, row('c', {}, {}, 'no bars')], [])).toEqual([up]);
  });
});

beforeEach(() => {
  scriptsApi.screen.mockReset();
  tradingApi.allDocuments.mockResolvedValue([
    { record_id: 'cross', status: 'active', payload: { name: 'SMA cross', source: '//@version=5\nindicator("x")' } },
    { record_id: 'old', status: 'archived', payload: { name: 'Old', source: '' } },
  ]);
  tradingApi.documents.mockResolvedValue([{ record_id: 'tech', payload: { schemaVersion: 2, name: 'Tech', items: [{ type: 'symbol', instrumentId: 'equity:NASDAQ:NVDA' }] } }]);
});

describe('script screener panel (TVP-11.6)', () => {
  it('screens the open charts with a saved script, then filters and sorts the results', async () => {
    scriptsApi.screen.mockResolvedValue({
      error: null,
      outputs: [{ key: 'plot:0', title: 'Fast', kind: 'plot' }, { key: 'alertcondition:3', title: 'Cross up', kind: 'alertcondition' }],
      rows: [
        row('equity:NASDAQ:AAPL', { 'plot:0': 9, 'alertcondition:3': 1 }, { 'plot:0': 6.5, 'alertcondition:3': 0 }),
        row('equity:NASDAQ:MSFT', { 'plot:0': 12, 'alertcondition:3': 0 }, { 'plot:0': 11, 'alertcondition:3': 0 }),
        row('equity:NASDAQ:BAD', {}, {}, 'no bars'),
      ],
    });
    const onShowInstrument = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <TradingScriptScreener chartInstrumentIds={['equity:NASDAQ:AAPL', 'equity:NASDAQ:MSFT', 'equity:NASDAQ:BAD']} onShowInstrument={onShowInstrument} />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole('option', { name: 'SMA cross' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Old' })).toBeNull();
    fireEvent.change(screen.getByRole('combobox', { name: 'Interval' }), { target: { value: '1h' } });
    fireEvent.click(screen.getByRole('button', { name: 'Screen' }));
    const table = await screen.findByRole('table', { name: 'Screen results' });
    expect(scriptsApi.screen).toHaveBeenCalledWith({ source: '//@version=5\nindicator("x")', instrumentIds: ['equity:NASDAQ:AAPL', 'equity:NASDAQ:MSFT', 'equity:NASDAQ:BAD'], interval: '1h' });
    expect(within(table).getAllByRole('row')).toHaveLength(3);
    expect(screen.getByText('1 not screened')).toBeInTheDocument();
    // Sort by Fast, largest first.
    fireEvent.click(within(table).getByRole('button', { name: 'Fast' }));
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('MSFT12');
    // A filter on the condition keeps the symbol where it fired.
    fireEvent.click(screen.getByRole('button', { name: 'Add filter' }));
    fireEvent.change(screen.getByRole('combobox', { name: 'Output' }), { target: { value: 'alertcondition:3' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Condition' }), { target: { value: 'true' } });
    expect(screen.getByRole('status')).toHaveTextContent('1 of 2 match');
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('AAPL9✓');
    fireEvent.click(within(table).getByRole('button', { name: 'AAPL' }));
    expect(onShowInstrument).toHaveBeenCalledWith('equity:NASDAQ:AAPL');
  });

  it('reports a script that does not compile', async () => {
    scriptsApi.screen.mockResolvedValue({ outputs: [], rows: [], error: { kind: 'syntax', message: 'unknown name nosuch', line: 3, column: 6 } });
    render(<QueryClientProvider client={new QueryClient()}><TradingScriptScreener chartInstrumentIds={['equity:NASDAQ:AAPL']} /></QueryClientProvider>);
    await screen.findByRole('option', { name: 'SMA cross' });
    fireEvent.click(screen.getByRole('button', { name: 'Screen' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('The script has a problem on line 3: unknown name nosuch');
  });
});
