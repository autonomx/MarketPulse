import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const replayApi = vi.hoisted(() => ({
  datasets: vi.fn(),
  freeze: vi.fn(),
  runBacktest: vi.fn(),
}));

vi.mock('./tradingReplayApi', () => ({ tradingReplayApi: replayApi }));

import { TradingReplayPanel } from './TradingReplayPanel';
import { useTradingReplayStore } from './tradingReplayStore';

const bars = Array.from({ length: 50 }, (_, index) => ({
  start_time: new Date(Date.UTC(2026, 7, 5, 12, index)).toISOString(),
  end_time: new Date(Date.UTC(2026, 7, 5, 12, index + 1)).toISOString(),
  open: '1',
  high: '1',
  low: '1',
  close: String(index),
  volume: '1',
}));

describe('frozen dataset replay panel', () => {
  beforeEach(() => {
    useTradingReplayStore.getState().clear();
    useTradingReplayStore.getState().setSpeed(1);
    replayApi.datasets.mockResolvedValue([{
      dataset_id: 'dataset-1',
      instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
      interval: '1m',
      dataset_fingerprint: 'abcdef0123456789',
      bars,
    }]);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('uses the shared speed setting instead of a free-text speed', async () => {
    render(<TradingReplayPanel instrumentId="crypto:BINANCE:spot:BTC-USDT" bindingId={null} interval="1m" />);
    const speed = await screen.findByRole('combobox', { name: 'Replay speed' });
    expect(screen.queryByRole('textbox', { name: /speed/i })).not.toBeInTheDocument();
    act(() => useTradingReplayStore.getState().setSpeed(10));
    expect(speed).toHaveValue('10');
    fireEvent.change(speed, { target: { value: '0.5' } });
    expect(useTradingReplayStore.getState().speed).toBe(0.5);
  });

  it('plays at the shared speed in bars per second', async () => {
    render(<TradingReplayPanel instrumentId="crypto:BINANCE:spot:BTC-USDT" bindingId={null} interval="1m" />);
    await screen.findByRole('combobox', { name: 'Replay speed' });
    vi.useFakeTimers();
    act(() => useTradingReplayStore.getState().setSpeed(30));
    fireEvent.click(screen.getByRole('button', { name: 'Play' }));
    act(() => { vi.advanceTimersByTime(1_000); });
    // 30× plays 3 bars per 100 ms tick: 30 bars in one second.
    expect(screen.getByText('30/50')).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(1_000); });
    expect(screen.getByText('50/50')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument();
  });
});
