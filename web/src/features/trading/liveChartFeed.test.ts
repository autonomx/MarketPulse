import { describe, expect, it } from 'vitest';
import { autoChartBindingId, bindingStreamsInterval, chartBindingId } from './liveChartFeed';
import type { ProviderBinding, ProviderDescriptor } from './tradingTypes';
import { fixture } from '../../test/fixture';

const AAPL = 'equity:NASDAQ:AAPL';
const ibkrBinding = fixture<ProviderBinding>({ binding_id: 'ibkr-aapl', instrument_id: AAPL, provider: 'ibkr', feed_type: 'socket', supported_intervals: ['1m'] });

function providers(ibkr: Partial<ProviderDescriptor> = {}): ProviderDescriptor[] {
  return [
    fixture<ProviderDescriptor>({ provider: 'yahoo', enabled: true, status: 'ready', bindings: [] }),
    fixture<ProviderDescriptor>({ provider: 'ibkr', enabled: true, status: 'ready', bindings: [ibkrBinding], ...ibkr }),
  ];
}

describe('which feed a chart reads', () => {
  it('uses IBKR for intraday stock charts up to 30m while the Gateway is connected', () => {
    for (const interval of ['1m', '3m', '5m', '15m', '30m']) {
      expect(autoChartBindingId(AAPL, interval, providers())).toBe('ibkr-aapl');
    }
  });

  it('keeps the default feed for longer intervals, other assets and a Gateway that is not connected', () => {
    expect(autoChartBindingId(AAPL, '1h', providers())).toBeNull();
    expect(autoChartBindingId(AAPL, '1d', providers())).toBeNull();
    expect(autoChartBindingId('crypto:BINANCE:spot:BTC-USDT', '1m', providers())).toBeNull();
    expect(autoChartBindingId(AAPL, '1m', providers({ status: 'unavailable' }))).toBeNull();
    expect(autoChartBindingId(AAPL, '1m', providers({ enabled: false }))).toBeNull();
    expect(autoChartBindingId(AAPL, '1m', providers({ bindings: [] }))).toBeNull();
    expect(autoChartBindingId(AAPL, '1m', undefined)).toBeNull();
  });

  it('never overrides a feed the user chose', () => {
    expect(chartBindingId({ instrumentId: AAPL, interval: '1m', bindingId: 'yahoo-aapl' }, providers())).toBe('yahoo-aapl');
    expect(chartBindingId({ instrumentId: AAPL, interval: '1m', bindingId: null }, providers())).toBe('ibkr-aapl');
  });
});

describe('which bindings stream live bars', () => {
  it('streams native websocket intervals and any intraday IBKR interval', () => {
    const binance = fixture<ProviderBinding>({ provider: 'binance', feed_type: 'websocket_and_rest', supported_intervals: ['1m', '5m'] });
    expect(bindingStreamsInterval(binance, '5m')).toBe(true);
    expect(bindingStreamsInterval(binance, '7m')).toBe(false);
    expect(bindingStreamsInterval(ibkrBinding, '7m')).toBe(true);
    expect(bindingStreamsInterval(ibkrBinding, '4h')).toBe(true);
    expect(bindingStreamsInterval(ibkrBinding, '1d')).toBe(false);
    expect(bindingStreamsInterval(ibkrBinding, '30s')).toBe(false);
    expect(bindingStreamsInterval(fixture<ProviderBinding>({ provider: 'yahoo', feed_type: 'historical_polling', supported_intervals: ['1m'] }), '1m')).toBe(false);
  });
});
