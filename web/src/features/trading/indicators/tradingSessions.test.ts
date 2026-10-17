import { describe, expect, it } from 'vitest';
import { UTC_SESSION, sessionForInstrument } from './tradingSessions';

describe('instrument sessions', () => {
  it('rolls futures at 18:00 ET even though the catalog tags them 24x7', () => {
    const goldFutures = { asset_class: 'commodity', instrument_type: 'perpetual', session_calendar: '24x7', exchange_timezone: 'America/Chicago' };
    expect(sessionForInstrument(goldFutures)).toEqual({ timezone: 'America/New_York', startMinute: 1080 });
  });

  it('keeps crypto and other 24x7 markets on UTC days', () => {
    expect(sessionForInstrument({ asset_class: 'crypto', session_calendar: '24x7' })).toBe(UTC_SESSION);
    expect(sessionForInstrument({ asset_class: 'index', session_calendar: '24x7' })).toBe(UTC_SESSION);
    expect(sessionForInstrument(null)).toBe(UTC_SESSION);
  });

  it('uses the regular New York session for US equities', () => {
    expect(sessionForInstrument({ asset_class: 'equity', session_calendar: 'XNYS', exchange_timezone: 'America/New_York' }))
      .toMatchObject({ timezone: 'America/New_York', regularStartMinute: 570, regularOnly: true });
  });
});
