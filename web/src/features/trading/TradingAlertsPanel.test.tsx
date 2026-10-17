import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const tradingApi = vi.hoisted(() => ({
  alerts: vi.fn(),
  alertTriggers: vi.fn(),
  updateAlert: vi.fn(),
  archiveAlert: vi.fn(),
  createAlert: vi.fn(),
  documents: vi.fn(async () => [{ record_id: 'wl-1', payload: { name: 'Tech' } }]),
  watchlistAlertCapacity: vi.fn(async () => ({ watchlist_id: 'wl-1', symbol_count: 150, provider_cap: 45, default_limit: 100 })),
}));

vi.mock('./tradingApi', () => ({ tradingApi }));
vi.mock('./useTradingAlerts', () => ({
  useTradingAlertMutations: () => ({ remove: vi.fn(), refresh: vi.fn(async () => undefined) }),
}));

import { TradingAlertsPanel } from './TradingAlertsPanel';

const conditionsAlert = {
  alert_id: 'multi-1',
  instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
  binding_id: null,
  condition_type: 'conditions',
  threshold: '0',
  frequency: 'once_per_bar_close',
  conditions: [
    { source: { kind: 'price', field: 'close' }, operator: 'crossing_up', target: { kind: 'value', value: '100' } },
    { source: { kind: 'indicator', indicator_id: 'rsi', inputs: { period: 14 }, output: 'rsi:14' }, operator: 'greater_than', target: { kind: 'value', value: '70' } },
  ],
  parameters: { message: '', notification_channels: ['app'], trigger_policy: 'once_per_bar_close' },
  evaluation_policy: { interval: '1h', allow_partial_bars: false, formula_version: 'omnix-indicators-v2' },
  enabled: true,
  cooldown_seconds: 0,
  revision: 2,
  created_at: '2026-10-08T09:00:00Z',
};

const trigger = {
  trigger_id: 't-1',
  alert_id: 'gone',
  instrument_id: 'crypto:BINANCE:spot:ETH-USDT',
  observed_value: '1',
  observed_price: '1',
  threshold: '0',
  condition_type: 'conditions',
  observed_at: '2026-10-08T09:00:00Z',
  evaluated_at: '2026-10-08T09:00:01Z',
  idempotency_key: 'k',
  payload: {},
};

describe('TradingAlertsPanel with alerts described by conditions', () => {
  it('lists, describes and edits them, keeping conditions the user did not change', async () => {
    tradingApi.alerts.mockResolvedValue([conditionsAlert]);
    tradingApi.alertTriggers.mockResolvedValue([trigger]);
    tradingApi.updateAlert.mockResolvedValue(conditionsAlert);
    render(<TradingAlertsPanel instrumentId={conditionsAlert.instrument_id} bindingId={null} />);

    const title = 'BTCUSDT Price crossing up 100.00 and rsi:14 greater than 70.00';
    expect(await screen.findByTitle(title)).toBeTruthy();
    fireEvent.mouseEnter(screen.getByTitle(title).closest('li') as HTMLElement);
    expect(screen.getByRole('tooltip').textContent).toContain('Trigger: once per bar close');

    fireEvent.click(screen.getByRole('button', { name: `Options for ${title}` }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit alert' }));
    // Each condition is an editable row (TVP-1.6).
    expect((screen.getByLabelText('Condition 1 operator') as HTMLSelectElement).value).toBe('crossing_up');
    expect((screen.getByLabelText('Condition 1 value') as HTMLInputElement).value).toBe('100');
    expect((screen.getByLabelText('Condition 2 source') as HTMLSelectElement).value).toBe('indicator');
    expect((screen.getByLabelText('Condition 2 line') as HTMLSelectElement).value).toBe('rsi:14');
    expect(screen.queryByLabelText('Alert value')).toBeNull();
    const triggerSelect = screen.getByLabelText('Alert trigger') as HTMLSelectElement;
    expect(triggerSelect.value).toBe('once_per_bar_close');
    fireEvent.change(triggerSelect, { target: { value: 'once_per_minute' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(tradingApi.updateAlert).toHaveBeenCalled());
    const input = tradingApi.updateAlert.mock.calls[0][1];
    expect(input.condition_type).toBe('conditions');
    expect(input.conditions).toEqual(conditionsAlert.conditions);
    expect(input.frequency).toBe('once_per_minute');
    expect(input.cooldown_seconds).toBe(0);

    fireEvent.click(screen.getByRole('tab', { name: /Log/ }));
    expect(await screen.findByTitle('ETHUSDT conditions met 0.00')).toBeTruthy();
  });
});

describe('TradingAlertsPanel multi-condition editing (TVP-1.6)', () => {
  it('saves an edited condition and a removed one', async () => {
    tradingApi.alerts.mockResolvedValue([conditionsAlert]);
    tradingApi.alertTriggers.mockResolvedValue([]);
    tradingApi.updateAlert.mockResolvedValue(conditionsAlert);
    render(<TradingAlertsPanel instrumentId={conditionsAlert.instrument_id} bindingId={null} />);
    const title = 'BTCUSDT Price crossing up 100.00 and rsi:14 greater than 70.00';
    fireEvent.click(await screen.findByRole('button', { name: `Options for ${title}` }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit alert' }));
    fireEvent.change(screen.getByLabelText('Condition 2 value'), { target: { value: '75' } });
    fireEvent.change(screen.getByLabelText('Condition 1 operator'), { target: { value: 'inside_channel' } });
    fireEvent.change(screen.getByLabelText('Condition 1 upper'), { target: { value: '110' } });
    fireEvent.change(screen.getByLabelText('Condition 1 lower'), { target: { value: '90' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(tradingApi.updateAlert).toHaveBeenCalled());
    const input = tradingApi.updateAlert.mock.calls.at(-1)![1];
    expect(input.conditions).toEqual([
      { source: { kind: 'price', field: 'close' }, operator: 'inside_channel', target: { kind: 'channel', upper: { kind: 'value', value: '110' }, lower: { kind: 'value', value: '90' } } },
      { source: { kind: 'indicator', indicator_id: 'rsi', inputs: { period: 14 }, output: 'rsi:14' }, operator: 'greater_than', target: { kind: 'value', value: '75' } },
    ]);
    // Remove the second: one condition stays (it can't be removed).
    fireEvent.click(screen.getByRole('button', { name: `Options for ${title}` }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit alert' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove condition 2' }));
    expect(screen.queryByRole('button', { name: 'Remove condition 1' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(tradingApi.updateAlert.mock.calls.at(-1)![1].conditions).toHaveLength(1));
  });
});

describe('TradingAlertsPanel conditions it cannot edit (TVP-1.6)', () => {
  it('saves a trendline conditions alert with its conditions unchanged', async () => {
    const trendline = {
      ...conditionsAlert,
      alert_id: 'trend-1',
      conditions: [
        { source: { kind: 'price', field: 'close' }, operator: 'crossing', target: { kind: 'source', source: { kind: 'trendline', points: [{ time: '2026-10-08T09:00:00Z', price: '1' }, { time: '2026-10-08T10:00:00Z', price: '2' }] } } },
      ],
    };
    tradingApi.alerts.mockResolvedValue([trendline]);
    tradingApi.alertTriggers.mockResolvedValue([]);
    tradingApi.updateAlert.mockResolvedValue(trendline);
    render(<TradingAlertsPanel instrumentId={trendline.instrument_id} bindingId={null} />);
    fireEvent.click(await screen.findByRole('button', { name: /^Options for / }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit alert' }));
    expect(screen.queryByLabelText('Condition 1 source')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(tradingApi.updateAlert).toHaveBeenCalled());
    expect(tradingApi.updateAlert.mock.calls.at(-1)![1].conditions).toEqual(trendline.conditions);
  });

  it('shows why a condition can\'t be saved', async () => {
    tradingApi.alerts.mockResolvedValue([conditionsAlert]);
    tradingApi.alertTriggers.mockResolvedValue([]);
    render(<TradingAlertsPanel instrumentId={conditionsAlert.instrument_id} bindingId={null} />);
    const title = 'BTCUSDT Price crossing up 100.00 and rsi:14 greater than 70.00';
    fireEvent.click(await screen.findByRole('button', { name: `Options for ${title}` }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit alert' }));
    fireEvent.change(screen.getByLabelText('Condition 1 value'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect((await screen.findByRole('alert')).textContent).toBe('Each condition needs a value or a line.');
  });
});

describe('TradingAlertsPanel watchlist alerts (TVP-1.7)', () => {
  it('creates an alert on every symbol of a watchlist, on each symbol own feed', async () => {
    tradingApi.alerts.mockResolvedValue([]);
    tradingApi.alertTriggers.mockResolvedValue([]);
    tradingApi.createAlert.mockImplementation(async (input) => ({ ...input, enabled: true, revision: 1, definition_revision: 1 }));
    render(<TradingAlertsPanel instrumentId="equity:NASDAQ:AAPL" bindingId="alpaca:AAPL" interval="1h" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add alert' }));
    const target = await screen.findByLabelText('Alert applies to');
    await waitFor(() => expect(screen.getByRole('option', { name: 'Watchlist: Tech' })).toBeTruthy());
    fireEvent.change(target, { target: { value: 'watchlist:wl-1' } });
    expect(await screen.findByText(/Runs on 45 of the list's 150 symbols \(at most 45 within its providers' request budgets\)/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Alert value'), { target: { value: '100' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create alert' }));
    await waitFor(() => expect(tradingApi.createAlert).toHaveBeenCalled());
    const input = tradingApi.createAlert.mock.calls.at(-1)![0];
    expect(input.instrument_id).toBe('watchlist:wl-1');
    expect(input.binding_id ?? null).toBeNull();
  });
});
