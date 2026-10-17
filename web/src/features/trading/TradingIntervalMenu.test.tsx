import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { resolveCustomInterval, TradingIntervalMenu } from './TradingIntervalMenu';
import { useTradingStore } from './tradingStore';

const binance = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '1d', '1w', '1mo'];

describe('TradingIntervalMenu', () => {
  beforeEach(() => {
    useTradingStore.setState({ favoriteIntervals: ['1h', '2h', '4h'] });
  });

  it('shows favourite intervals as quick buttons', () => {
    render(<TradingIntervalMenu interval="1h" supportedIntervals={binance} onSelect={vi.fn()} />);
    const group = screen.getByRole('group', { name: 'Trading timeframe' });
    expect(within(group).getByRole('button', { name: '1H' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(group).getByRole('button', { name: '2H' })).toBeInTheDocument();
    expect(within(group).getByRole('button', { name: '4H' })).toBeInTheDocument();
  });

  it('applies a typed custom interval and saves it to favourites', () => {
    const onSelect = vi.fn();
    render(<TradingIntervalMenu interval="1h" supportedIntervals={binance} onSelect={onSelect} />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Custom interval' }), { target: { value: '7m' } });
    fireEvent.submit(screen.getByRole('form', { name: 'Custom interval' }));
    expect(onSelect).toHaveBeenCalledWith('7m');
    expect(useTradingStore.getState().favoriteIntervals).toEqual(['7m', '1h', '2h', '4h']);
    expect(screen.getByRole('button', { name: '7m' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Custom' })).toHaveTextContent('7 minutes');
  });

  it('refuses intervals the feed cannot serve and says why', () => {
    const onSelect = vi.fn();
    render(<TradingIntervalMenu interval="1h" supportedIntervals={binance} feedName="binance" onSelect={onSelect} />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Custom interval' }), { target: { value: '10s' } });
    fireEvent.submit(screen.getByRole('form', { name: 'Custom interval' }));
    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('Seconds need trade-level data, which binance does not provide.');
    fireEvent.change(screen.getByRole('textbox', { name: 'Custom interval' }), { target: { value: '7x' } });
    fireEvent.submit(screen.getByRole('form', { name: 'Custom interval' }));
    expect(screen.getByRole('alert')).toHaveTextContent('is not an interval');
  });

  it('stars and unstars catalogue intervals', () => {
    render(<TradingIntervalMenu interval="1h" supportedIntervals={binance} onSelect={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Add 15 minutes to favourite intervals' }));
    expect(useTradingStore.getState().favoriteIntervals).toEqual(['15m', '1h', '2h', '4h']);
    fireEvent.click(screen.getByRole('button', { name: 'Remove 2 hours from favourite intervals' }));
    expect(useTradingStore.getState().favoriteIntervals).toEqual(['15m', '1h', '4h']);
  });

  it('resolves typed intervals against the feed', () => {
    expect(resolveCustomInterval('3h', binance)).toEqual({ interval: '3h' });
    expect(resolveCustomInterval('2D', binance)).toEqual({ interval: '2d' });
    expect(resolveCustomInterval('500t', binance)).toEqual({ error: 'Ticks need trade-level data, which the selected feed does not provide.' });
  });
});
