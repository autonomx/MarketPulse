import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render as renderPlain, screen } from '@testing-library/react';
import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TradingIndicatorSettings } from './TradingIndicatorSettings';

afterEach(cleanup);

const render = (element: ReactElement) => renderPlain(<QueryClientProvider client={new QueryClient()}>{element}</QueryClientProvider>);

describe('indicator source input (TVP-6.5)', () => {
  it('reads another indicator line instead of the close', () => {
    const onApply = vi.fn();
    render(
      <TradingIndicatorSettings
        indicator={{ id: 'sma', period: 10, enabled: true }}
        sourceChoices={[{ value: 'rsi:14', label: 'RSI', ref: { indicatorId: 'rsi', output: 'rsi:14' } }]}
        onApply={onApply}
        onClose={() => undefined}
      />,
    );
    const select = screen.getByLabelText('Indicator source') as HTMLSelectElement;
    expect(select.value).toBe('');
    fireEvent.change(select, { target: { value: 'rsi:14' } });
    fireEvent.click(screen.getByRole('button', { name: 'OK' }));
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ source: { indicatorId: 'rsi', output: 'rsi:14' } }));
  });

  it('has no source input for an indicator that takes none', () => {
    render(<TradingIndicatorSettings indicator={{ id: 'atr', period: 14, enabled: true }} onApply={() => undefined} onClose={() => undefined} />);
    expect(screen.queryByLabelText('Indicator source')).toBeNull();
  });
});

describe('removing a source indicator (TVP-6.5 review)', () => {
  it('sets what read it back to the close', async () => {
    const { useTradingStore } = await import('./tradingStore');
    const store = useTradingStore.getState();
    const chartId = store.charts[0].chartId;
    const enabled = (id: 'rsi' | 'sma') => useTradingStore.getState().charts[0].indicators.some((indicator) => indicator.id === id && indicator.enabled);
    if (!enabled('rsi')) store.toggleIndicator(chartId, 'rsi');
    if (!enabled('sma')) store.toggleIndicator(chartId, 'sma');
    useTradingStore.getState().updateIndicator(chartId, 'sma', { source: { indicatorId: 'rsi', output: 'rsi:14' } });
    useTradingStore.getState().toggleIndicator(chartId, 'rsi');
    const sma = useTradingStore.getState().charts[0].indicators.find((indicator) => indicator.id === 'sma');
    expect(sma?.source).toBeNull();
  });

  it('draws an alert on an overlay of a pane indicator in that pane', async () => {
    const { chartAlertSourceIndicatorId } = await import('./alertIndicatorSources');
    const alert = { condition_type: 'conditions', threshold: 0, parameters: {}, conditions: [{ source: { kind: 'indicator', indicator_id: 'sma', inputs: { period: 5, source: { indicator_id: 'rsi', inputs: { period: 14 }, output: 'rsi:14' } }, output: 'sma:5' }, operator: 'crossing', target: { kind: 'value', value: '50' } }] };
    expect(chartAlertSourceIndicatorId(alert)).toBe('rsi');
  });
});
