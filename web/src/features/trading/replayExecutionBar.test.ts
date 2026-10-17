import { describe, expect, it } from 'vitest';
import { replayExecutionBar } from './tradingReplayApi';
import type { MarketBar } from './tradingTypes';
import { fixture } from '../../test/fixture';

const bar = fixture<MarketBar>({
  instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
  interval: '5m',
  start_time: '2026-08-05T12:00:00.000Z',
  end_time: '2026-08-05T12:05:00.000Z',
  open: '1', high: '2', low: '1', close: '2', volume: '3',
});

describe('replay execution bar', () => {
  it('carries the session binding and the bar close', () => {
    expect(replayExecutionBar({ ...bar, binding_id: 'bind-1' })).toMatchObject({
      binding_id: 'bind-1', end_time: '2026-08-05T12:05:00.000Z',
    });
    expect(replayExecutionBar(bar).binding_id).toBeNull();
  });

  it('sends an unreadable end as the derived close', () => {
    expect(replayExecutionBar({ ...bar, end_time: 'not a time' }).end_time).toBe('2026-08-05T12:05:00.000Z');
  });

  it('refuses a bar whose close cannot be known', () => {
    expect(() => replayExecutionBar({ ...bar, end_time: '', interval: 'tick' })).toThrow(/no known close/);
  });
});
