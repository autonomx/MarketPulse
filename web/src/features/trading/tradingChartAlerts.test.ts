import { describe, expect, it } from 'vitest';
import {
  alertConditionsSummary,
  alertFrequency,
  alertVisualState,
  chartAlertCreateInput,
  chartAlertUpdateInput,
  expirationTimestamp,
  formatAlertThreshold,
  priceConditionForThreshold,
} from './tradingChartAlerts';
import type { TradingAlert } from './tradingTypes';
import { fixture } from '../../test/fixture';

const baseAlert: TradingAlert = fixture({
  alert_id: 'chart-alert-1',
  instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
  binding_id: 'binance:websocket_and_rest:crypto:BINANCE:spot:BTC-USDT',
  condition_type: 'price_above',
  threshold: '70000',
  parameters: {
    lookback_bars: 1,
    indicator_id: null,
    period: 14,
    fast_period: 12,
    slow_period: 26,
    signal_period: 9,
    component: 'value',
    anchor_bars_ago: 0,
  },
  evaluation_policy: {
    interval: '2h',
    allow_partial_bars: false,
    formula_version: 'omnix-indicators-v2',
  },
  enabled: true,
  cooldown_seconds: 0,
  revision: 3,
});

describe('chart-native Trading alerts', () => {
  it('distinguishes lifecycle state from a transient trigger highlight', () => {
    const now = Date.parse('2026-08-06T07:00:00Z');
    expect(alertVisualState(baseAlert, now)).toBe('active');
    expect(alertVisualState({ ...baseAlert, last_triggered_at: '2026-08-06T06:59:50Z' }, now)).toBe('triggered');
    expect(alertVisualState({ ...baseAlert, last_triggered_at: '2026-08-06T06:59:00Z' }, now)).toBe('active');
    expect(alertVisualState({ ...baseAlert, enabled: false }, now)).toBe('disabled');
    expect(alertVisualState({ ...baseAlert, enabled: false, expires_at: '2026-08-06T06:00:00Z' }, now)).toBe('expired');
  });

  it('chooses the crossing direction from the placed chart price', () => {
    expect(priceConditionForThreshold(101, 100)).toBe('price_above');
    expect(priceConditionForThreshold(99, 100)).toBe('price_below');
  });

  it('builds a server-owned alert from the active chart contract', () => {
    const input = chartAlertCreateInput({
      alertId: 'chart-alert-2',
      instrumentId: baseAlert.instrument_id,
      bindingId: baseAlert.binding_id ?? null,
      interval: '2h',
      threshold: 71000,
      latestPrice: 70000,
      expiration: '1d',
      now: Date.parse('2026-08-06T07:00:00Z'),
    });
    expect(input.condition_type).toBe('price_above');
    expect(input.evaluation_policy?.interval).toBe('2h');
    expect(input.expires_at).toBe('2026-08-07T07:00:00.000Z');
    expect(expirationTimestamp('never')).toBeNull();
  });

  it('sends the trigger choice as the server-enforced frequency', () => {
    const input = chartAlertCreateInput({
      alertId: 'chart-alert-policy',
      instrumentId: baseAlert.instrument_id,
      bindingId: baseAlert.binding_id ?? null,
      interval: '5m',
      threshold: 71_000,
      latestPrice: 70_000,
      expiration: 'never',
      triggerPolicy: 'once_per_bar',
      message: 'Watch the breakout',
      notificationChannels: ['app', 'toast'],
    });
    expect(input.frequency).toBe('once_per_bar');
    expect(input.evaluation_policy.allow_partial_bars).toBe(true);
    expect(input.cooldown_seconds).toBe(0);
    expect(input.parameters.trigger_policy).toBe('once_per_bar');
    expect(input.parameters.message).toBe('Watch the breakout');
  });

  it('changes the frequency on update and clears the old cooldown approximation', () => {
    const legacy = { ...baseAlert, cooldown_seconds: 7_200, parameters: { ...baseAlert.parameters, trigger_policy: 'once_per_bar' as const } };
    expect(alertFrequency(legacy)).toBe('once_per_bar');
    const unchanged = chartAlertUpdateInput(legacy, { enabled: false });
    expect(unchanged.frequency).toBe('once_per_bar');
    expect(unchanged.cooldown_seconds).toBe(7_200);
    const input = chartAlertUpdateInput(legacy, { trigger_policy: 'once_per_bar_close' });
    expect(input.frequency).toBe('once_per_bar_close');
    expect(input.evaluation_policy.allow_partial_bars).toBe(false);
    expect(input.parameters.trigger_policy).toBe('once_per_bar_close');
    expect(input.cooldown_seconds).toBe(0);
    expect(input).not.toHaveProperty('conditions');
  });

  it('keeps the conditions of alerts described by conditions', () => {
    const conditions = [
      { source: { kind: 'price', field: 'close' }, operator: 'crossing_up', target: { kind: 'value', value: '100' } },
      {
        source: { kind: 'indicator', indicator_id: 'rsi', inputs: { period: 14 }, output: 'rsi:14' },
        operator: 'inside_channel',
        target: { kind: 'channel', upper: { kind: 'value', value: '70' }, lower: { kind: 'value', value: '30' } },
      },
      { source: { kind: 'change_percent', lookback_bars: 3 }, operator: 'moving_up_percent', amount: '2', bars: 5 },
    ] as unknown as TradingAlert['conditions'];
    const alert = fixture<TradingAlert>({ ...baseAlert, condition_type: 'conditions', threshold: '0', frequency: 'once_per_minute', conditions });
    const input = chartAlertUpdateInput(alert, { enabled: false });
    expect(input.condition_type).toBe('conditions');
    expect(input.conditions).toEqual(conditions);
    expect(input.frequency).toBe('once_per_minute');
    expect(alertConditionsSummary(alert)).toBe(
      'Price crossing up 100.00 and rsi:14 inside channel 30.00 – 70.00 and Change % (3) moving up % 2% in 5 bars',
    );
  });

  it('preserves policy and revision-owned fields when dragging a threshold', () => {
    const input = chartAlertUpdateInput(baseAlert, { threshold: '72000' });
    expect(input.threshold).toBe('72000');
    expect(input.evaluation_policy.interval).toBe('2h');
    expect(input.parameters).toEqual(baseAlert.parameters);
    expect(input.enabled).toBe(true);
  });

  it('sends the Sound channel sound and keeps the other delivery settings', () => {
    const create = chartAlertCreateInput({
      alertId: 'chart-alert-3',
      instrumentId: baseAlert.instrument_id,
      bindingId: null,
      interval: '1m',
      threshold: 1,
      latestPrice: 2,
      expiration: 'never',
      notificationChannels: ['app', 'sound'],
      soundName: 'alarm',
    });
    expect(create.parameters.delivery).toEqual({ sound: { name: 'alarm' } });
    const silent = chartAlertCreateInput({
      alertId: 'chart-alert-4', instrumentId: baseAlert.instrument_id, bindingId: null, interval: '1m',
      threshold: 1, latestPrice: 2, expiration: 'never', notificationChannels: ['app'], soundName: 'alarm',
    });
    expect(silent.parameters.delivery).toBeUndefined();
    const hooked = fixture<TradingAlert>({
      ...baseAlert,
      parameters: { ...baseAlert.parameters, delivery: { webhook: { display_url: 'https://hooks.example', has_secret: true } } },
    });
    const update = chartAlertUpdateInput(hooked, { sound_name: 'beep' });
    expect(update.parameters.delivery).toEqual({ webhook: { display_url: 'https://hooks.example', has_secret: true }, sound: { name: 'beep' } });
    expect(chartAlertUpdateInput(hooked, { enabled: false }).parameters.delivery).toEqual(hooked.parameters.delivery);
  });

  it('sends a name and a webhook only with its channel; a stored webhook is kept unless replaced (TVP-1.5)', () => {
    const base = { alertId: 'w', instrumentId: baseAlert.instrument_id, bindingId: null, interval: '1m', threshold: 1, latestPrice: 2, expiration: 'never' as const };
    const hooked = chartAlertCreateInput({ ...base, notificationChannels: ['app', 'webhook'], name: 'Breakout', webhookUrl: 'https://hooks.example/x', webhookSecret: 's3' });
    expect(hooked.parameters.name).toBe('Breakout');
    expect(hooked.parameters.delivery).toEqual({ webhook: { url: 'https://hooks.example/x' } });
    expect(hooked.webhook_secret).toBe('s3');
    const unhooked = chartAlertCreateInput({ ...base, notificationChannels: ['app'], webhookUrl: 'https://hooks.example/x', webhookSecret: 's3' });
    expect(unhooked.parameters.delivery).toBeUndefined();
    expect(unhooked.webhook_secret).toBeUndefined();
    const stored = fixture<TradingAlert>({
      ...baseAlert,
      parameters: { ...baseAlert.parameters, delivery: { webhook: { display_url: 'https://hooks.example', has_secret: false } } },
    });
    expect(chartAlertUpdateInput(stored, { name: 'x' }).parameters.delivery).toEqual(stored.parameters.delivery);
    expect(chartAlertUpdateInput(stored, { webhook_url: 'https://other.example/y' }).parameters.delivery).toEqual({ webhook: { url: 'https://other.example/y' } });
    expect(chartAlertUpdateInput(stored, { webhook_secret: 'new' }).webhook_secret).toBe('new');
    expect(chartAlertUpdateInput(baseAlert, { webhook_secret: 'orphan' }).webhook_secret).toBeUndefined();
  });

  it('formats alert values to two decimal places', () => {
    expect(formatAlertThreshold(73.16472733528584)).toBe('73.16');
    expect(formatAlertThreshold('73')).toBe('73.00');
    expect(formatAlertThreshold('')).toBe('');
  });
});

describe('chart-native Trading alerts', () => {
  it('persists the indicator identity when an alert is changed to RSI', () => {
    const input = chartAlertUpdateInput(baseAlert, {
      condition_type: 'indicator_cross_below',
      indicator_id: 'rsi',
      period: 14,
      threshold: '73',
    });

    expect(input.condition_type).toBe('indicator_cross_below');
    expect(input.parameters.indicator_id).toBe('rsi');
    expect(input.parameters.period).toBe(14);
  });
});
