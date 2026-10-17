import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ alerts: [] as unknown[], triggers: [] as unknown[] }));
const play = vi.hoisted(() => vi.fn(() => true));
vi.mock('./useTradingAlerts', () => ({
  useTradingAlerts: () => ({ data: state.alerts }),
  useTradingAlertTriggers: () => ({ data: state.triggers }),
}));
vi.mock('./alertSounds', () => ({ playAlertSound: play }));

import { TradingAlertToastLayer } from './TradingAlertToastLayer';

const alert = (id: string, channels: string[], sound?: string) => ({
  alert_id: id,
  parameters: { notification_channels: channels, message: `${id} fired`, delivery: sound ? { sound: { name: sound } } : {} },
});
const trigger = (id: string, alertId: string) => ({ trigger_id: id, alert_id: alertId, instrument_id: 'crypto:X:spot:BTC-USDT', threshold: '1' });

beforeEach(() => {
  play.mockClear();
  state.alerts = [alert('loud', ['app', 'sound'], 'alarm'), alert('quiet', ['app', 'toast'])];
  state.triggers = [trigger('old', 'loud')];
});
afterEach(cleanup);

describe('TradingAlertToastLayer sounds', () => {
  it('plays a new sound trigger once, never the first snapshot or a refetch', () => {
    const view = render(<TradingAlertToastLayer />);
    expect(play).not.toHaveBeenCalled();
    state.triggers = [trigger('new', 'loud'), ...state.triggers];
    view.rerender(<TradingAlertToastLayer />);
    expect(play).toHaveBeenCalledTimes(1);
    expect(play).toHaveBeenCalledWith('alarm');
    state.alerts = [...state.alerts];
    view.rerender(<TradingAlertToastLayer />);
    expect(play).toHaveBeenCalledTimes(1);
  });

  it('plays the sound of an alert firing with a toast-only one, and shows the toast', () => {
    const view = render(<TradingAlertToastLayer />);
    state.triggers = [trigger('a', 'quiet'), trigger('b', 'loud'), ...state.triggers];
    act(() => view.rerender(<TradingAlertToastLayer />));
    expect(play).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('status').textContent).toContain('quiet fired');
  });

  it('shows the message the server rendered for the trigger (TVP-1.5)', () => {
    const view = render(<TradingAlertToastLayer />);
    state.triggers = [{ ...trigger('d', 'quiet'), payload: { message: 'BTCUSDT closed at 101.5' } }, ...state.triggers];
    act(() => view.rerender(<TradingAlertToastLayer />));
    expect(screen.getByRole('status').textContent).toContain('BTCUSDT closed at 101.5');
  });

  it('stays silent for alerts without the Sound channel', () => {
    const view = render(<TradingAlertToastLayer />);
    state.triggers = [trigger('c', 'quiet'), ...state.triggers];
    view.rerender(<TradingAlertToastLayer />);
    expect(play).not.toHaveBeenCalled();
  });
});
