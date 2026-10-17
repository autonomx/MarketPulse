import { beforeEach, describe, expect, it } from 'vitest';
import { useTradingReplayStore } from './tradingReplayStore';
import { useTradingStore } from './tradingStore';
import type { MarketBar } from './tradingTypes';
import { fixture } from '../../test/fixture';

const MINUTE = 60_000;
const BASE = Date.parse('2026-08-05T12:00:00Z');
const at = (minute: number) => BASE + minute * MINUTE;

function bar(startMinute: number, minutes: number): MarketBar {
  return fixture({
    instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
    interval: `${minutes}m`,
    start_time: new Date(at(startMinute)).toISOString(),
    end_time: new Date(at(startMinute + minutes)).toISOString(),
    open: '1',
    high: '1',
    low: '1',
    close: '1',
    volume: '1',
    is_final: true,
    adjustment_mode: 'raw',
    session: '24x7',
    provider: 'binance',
    ingestion_revision: 1,
    received_at: new Date(at(startMinute + minutes)).toISOString(),
  });
}

const fiveMinute = Array.from({ length: 6 }, (_, index) => bar(index * 5, 5));
const replay = () => useTradingReplayStore.getState();
const sessionId = () => useTradingStore.getState().replaySessionId;

describe('shared replay clock store', () => {
  beforeEach(() => {
    replay().clear();
    replay().setSpeed(1);
    replay().setActiveBars(fiveMinute);
  });

  it('starts in start-bar selection with no clock', () => {
    expect(replay()).toMatchObject({ clock: null, startTime: null, selecting: true, playing: false });
    expect(replay().stepForward()).toBe(false);
    replay().setPlaying(true);
    expect(replay().playing).toBe(false);
  });

  it('choosing a start bar sets the clock and restarts replay trading', () => {
    const before = sessionId();
    replay().chooseStart(at(10));
    expect(replay()).toMatchObject({ clock: at(10), startTime: at(10), selecting: false, playing: false });
    expect(sessionId()).toBe(before + 1);
  });

  it('steps by one bar of the active chart and back to the start', () => {
    replay().chooseStart(at(10));
    expect(replay().stepForward()).toBe(true);
    expect(replay().clock).toBe(at(15));
    const before = sessionId();
    expect(replay().stepBack()).toBe(true);
    expect(replay().clock).toBe(at(10));
    expect(sessionId()).toBe(before + 1);
    expect(replay().stepBack()).toBe(false);
  });

  it('steps through the newly active chart after the active chart changes', () => {
    replay().chooseStart(at(10));
    replay().setActiveBars([bar(0, 30), bar(30, 30)]);
    replay().stepForward();
    expect(replay().clock).toBe(at(30));
  });

  it('plays until the active chart runs out of bars', () => {
    replay().chooseStart(at(20));
    replay().setPlaying(true);
    expect(replay().playing).toBe(true);
    replay().stepForward(3);
    expect(replay().clock).toBe(at(30));
    expect(replay().playing).toBe(false);
    replay().setPlaying(true);
    expect(replay().playing).toBe(false);
  });

  it('jumps to a new start bar during playback', () => {
    replay().chooseStart(at(5));
    replay().setPlaying(true);
    replay().beginSelecting();
    expect(replay()).toMatchObject({ selecting: true, playing: false, clock: at(5) });
    expect(replay().stepForward()).toBe(false);
    replay().chooseStart(at(20));
    expect(replay()).toMatchObject({ selecting: false, clock: at(20), startTime: at(20) });
  });

  it('resets to the start bar', () => {
    replay().chooseStart(at(5));
    replay().stepForward(2);
    replay().resetToStart();
    expect(replay()).toMatchObject({ clock: at(5), playing: false, selecting: false });
  });

  it('keeps the speed when replay is cleared', () => {
    replay().setSpeed(30);
    replay().chooseStart(at(5));
    replay().clear();
    expect(replay()).toMatchObject({ clock: null, startTime: null, selecting: true, speed: 30 });
  });
});
