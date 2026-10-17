import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { CoreIndicatorId, CoreIndicatorInstance } from './indicators/coreIndicators';
import type { MarketBar } from './tradingTypes';

const tradingApi = vi.hoisted(() => ({ instruments: vi.fn(), bars: vi.fn() }));
vi.mock('./tradingApi', () => ({ tradingApi }));

import { TradingBuiltInInputs } from './TradingBuiltInInputs';
import { comparisonBarsQueryKey, compareBarsLimit, loadCompareSymbolBars } from './tradingChartPanelModel';

const qqq = { instrument_id: 'equity:NASDAQ:QQQ', display_symbol: 'QQQ', venue_symbol: 'QQQ', venue: 'NASDAQ', asset_class: 'equity', instrument_type: 'equity', quote_currency: 'USD', base_currency: null };

function Harness({ initial, onValidity }: { initial: CoreIndicatorInstance; onValidity: (valid: boolean) => void }) {
  const [draft, setDraft] = useState(initial);
  return (
    <>
      <TradingBuiltInInputs draft={draft} currentInstrumentId="crypto:BINANCE:spot:BTC-USDT" setDraft={setDraft} onValidityChange={onValidity} />
      <output data-testid="draft">{JSON.stringify({ compareSymbol: draft.compareSymbol, params: draft.params, period: draft.period })}</output>
    </>
  );
}

function renderInputs(initial: CoreIndicatorInstance, onValidity = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><Harness initial={initial} onValidity={onValidity} /></QueryClientProvider>);
  return onValidity;
}

const correlation = (compareSymbol: string | null): CoreIndicatorInstance => ({ id: 'tv-correlation-coefficient-cc' as CoreIndicatorId, period: 20, enabled: true, compareSymbol });

afterEach(() => { vi.clearAllMocks(); });

describe('TradingBuiltInInputs', () => {
  it('chooses the compare symbol with the compare-symbol dialog', async () => {
    tradingApi.instruments.mockResolvedValue([qqq]);
    renderInputs(correlation(null));
    fireEvent.click(screen.getByRole('button', { name: 'Choose symbol…' }));
    expect(screen.getByRole('heading', { name: 'Choose symbol' })).toBeTruthy();
    expect(screen.queryByRole('group', { name: 'Comparison placement' })).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: /QQQ/ }));
    await waitFor(() => expect(screen.getByTestId('draft').textContent).toContain('equity:NASDAQ:QQQ'));
    expect(screen.queryByRole('heading', { name: 'Choose symbol' })).toBeNull();
  });

  it('shows an error and reports invalid inputs for a symbol outside the catalog', async () => {
    tradingApi.instruments.mockResolvedValue([qqq]);
    const onValidity = renderInputs(correlation('equity:NASDAQ:NOPE'));
    expect((await screen.findByRole('alert')).textContent).toContain('equity:NASDAQ:NOPE is not in the instrument catalog');
    expect(onValidity).toHaveBeenLastCalledWith(false);
  });

  it('edits declared params under their labels', () => {
    renderInputs({ id: 'tv-rob-booker-reversal' as CoreIndicatorId, period: 14, enabled: true });
    expect(screen.getByLabelText('K period')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Slowing'), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText('Stochastic upper'), { target: { value: '80' } });
    expect(JSON.parse(screen.getByTestId('draft').textContent ?? '{}').params).toEqual({ slowing: 5, upper: 80 });
  });

  it('hides the period where it has no meaning and offers the select options', () => {
    renderInputs({ id: 'tv-rob-booker-ziv-ghost-pivots' as CoreIndicatorId, period: 1, enabled: true });
    expect(screen.queryByRole('spinbutton')).toBeNull();
    fireEvent.change(screen.getByLabelText('Pivot period'), { target: { value: 'M' } });
    expect(JSON.parse(screen.getByTestId('draft').textContent ?? '{}').params).toEqual({ pivotPeriod: 'M' });
  });
});

describe('compare-symbol bars', () => {
  const bar = (start: string): MarketBar => ({ start_time: start, close: '1' } as MarketBar);

  it('loads enough bars to cover the chart time range, in a few cache sizes', () => {
    const now = Date.UTC(2026, 9, 8);
    expect(compareBarsLimit('crypto:BINANCE:spot:ETH-USDT', '1h', now - 500 * 3_600_000, now)).toBe(1_000);
    expect(compareBarsLimit('crypto:BINANCE:spot:ETH-USDT', '1h', now - 1_500 * 3_600_000, now)).toBe(2_000);
    expect(compareBarsLimit('crypto:BINANCE:spot:ETH-USDT', '1h', now - 9_000 * 3_600_000, now)).toBe(5_000);
  });

  it('shares the comparison query cache', async () => {
    const client = new QueryClient();
    const now = Date.UTC(2026, 9, 8);
    tradingApi.bars.mockResolvedValue({ bars: [bar('2026-10-07T00:00:00Z')], instrument: qqq, binding: {} });
    const range = { from: now - 1_500 * 3_600_000, to: now };
    const first = await loadCompareSymbolBars(client, 'equity:NASDAQ:QQQ', '1h', range, now);
    const second = await loadCompareSymbolBars(client, 'equity:NASDAQ:QQQ', '1h', range, now);
    expect(first).toEqual(second);
    expect(tradingApi.bars).toHaveBeenCalledTimes(1);
    expect(tradingApi.bars).toHaveBeenCalledWith('equity:NASDAQ:QQQ', '1h', 2_000, undefined, { alignment: 'clock', extendedHours: true });
    expect(client.getQueryData(comparisonBarsQueryKey('equity:NASDAQ:QQQ', '1h', 2_000))).toBeTruthy();
  });

  it('keeps regular-hours compare bars apart from extended-hours ones', async () => {
    const client = new QueryClient();
    const now = Date.UTC(2026, 9, 8);
    tradingApi.bars.mockResolvedValue({ bars: [bar('2026-10-07T00:00:00Z')], instrument: qqq, binding: {} });
    const range = { from: now - 1_500 * 3_600_000, to: now };
    await loadCompareSymbolBars(client, 'equity:NASDAQ:QQQ', '1h', range, now, true);
    await loadCompareSymbolBars(client, 'equity:NASDAQ:QQQ', '1h', range, now, false);
    expect(tradingApi.bars).toHaveBeenCalledTimes(2);
    expect(tradingApi.bars).toHaveBeenLastCalledWith('equity:NASDAQ:QQQ', '1h', 2_000, undefined, { alignment: 'clock', extendedHours: false });
    expect(client.getQueryData(comparisonBarsQueryKey('equity:NASDAQ:QQQ', '1h', 2_000, false))).toBeTruthy();
  });
});
