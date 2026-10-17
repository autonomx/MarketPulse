import { describe, expect, it } from 'vitest';
import { chartOrderLines, entryMoveInput, exitReplacement, moveErrorMessage, roundPrice } from './paperOrderLines';
import { INSTRUMENT, order, pendingStop } from '../../test/paperOrders';
import type { PaperOrder } from './paperTypes';

describe('chart order lines', () => {
  it('draws working orders of the chart instrument with side, type and quantity', () => {
    const lines = chartOrderLines([
      order(),
      order({ order_id: 'exit-1', side: 'sell', order_type: 'stop', limit_price: null, stop_price: '8.5', quantity: '40', filled_quantity: '10' }),
      order({ order_id: 'other', instrument_id: 'equity:NYSE:OTHER' }),
      order({ order_id: 'done', status: 'filled' }),
      order({ order_id: 'market', order_type: 'market', limit_price: null }),
    ], INSTRUMENT, [pendingStop]);
    expect(lines.map((line) => [line.label, line.price, line.move])).toEqual([
      ['BUY LMT 100', 10, 'entry'],
      ['SELL STP 30', 8.5, 'exit'],
    ]);
  });

  it('moves a short entry with its pending stop as an entry (TVP-7.2a)', () => {
    const shortEntry = order({ order_id: 'short-1', side: 'sell', limit_price: '12' });
    const stop = { ...pendingStop, entry_order_id: 'short-1', stop_loss: '13' } as typeof pendingStop;
    expect(chartOrderLines([shortEntry], INSTRUMENT, [stop])[0].move).toBe('entry');
    expect(chartOrderLines([shortEntry], INSTRUMENT, [])[0].move).toBe('exit');
  });

  it('leaves what the server would refuse to move undraggable', () => {
    const lines = chartOrderLines([
      order({ order_id: 'manual' }),
      order({ order_id: 'trail', side: 'sell', order_type: 'trailing_stop', limit_price: null, stop_price: '9' }),
      order({ filled_quantity: '5' }),
    ], INSTRUMENT, [pendingStop]);
    expect(lines.map((line) => line.move)).toEqual([null, null, null]);
  });

  it('draws a stop-limit at its stop until the stop is reached, then at its limit', () => {
    const stopLimit = order({ order_type: 'stop_limit', stop_price: '11', limit_price: '11.2' });
    expect(chartOrderLines([stopLimit], INSTRUMENT, [pendingStop])[0]).toMatchObject({ price: 11, label: 'BUY STP LMT 100', move: 'entry' });
    const triggered = { ...stopLimit, stop_triggered_at: '2026-10-08T15:00:00Z' } as PaperOrder;
    expect(chartOrderLines([triggered], INSTRUMENT, [pendingStop])[0]).toMatchObject({ price: 11.2, move: null });
  });

  it('rounds dragged prices to the tick', () => {
    expect(roundPrice(10.2371, 0.01)).toBe(10.24);
    expect(roundPrice(10.2371, 0.25)).toBe(10.25);
    expect(roundPrice(0.000123456789, null)).toBe(0.00012345679);
  });
});

describe('moving an order', () => {
  it('asks the server to re-price an entry under a new order id, keeping a stop-limit offset', () => {
    expect(entryMoveInput(order(), 9.5, 0.01, 36)).toEqual({ order_id: 'entry-1-moved-10', idempotency_key: 'entry-1-moved-10', trigger_price: '9.5', limit_price: null });
    const stopLimit = order({ order_type: 'stop_limit', stop_price: '11', limit_price: '11.2' });
    expect(entryMoveInput(stopLimit, 12, 0.01, 36).limit_price).toBe('12.2');
  });

  it('replaces an exit with the same order for what is left, at the new price', () => {
    const exit = order({ order_id: 'exit-1', side: 'sell', order_type: 'stop', limit_price: null, stop_price: '8.5', quantity: '40', filled_quantity: '10', time_in_force: 'gtc' });
    expect(exitReplacement(exit, 8.7, 0.01, 36)).toMatchObject({
      order_id: 'exit-1-moved-10', side: 'sell', order_type: 'stop', quantity: '30', stop_price: '8.7', limit_price: null, time_in_force: 'gtc', reference_price: null,
    });
    // A DAY order's stored expiry is not sent back (the server refuses an expiry outside GTD).
    expect(exitReplacement({ ...exit, time_in_force: 'day', expires_at: '2026-10-08T20:00:00Z' } as PaperOrder, 8.7, 0.01, 36).expires_at).toBeNull();
    expect(exitReplacement({ ...exit, time_in_force: 'gtd', expires_at: '2026-10-09T20:00:00Z' } as PaperOrder, 8.7, 0.01, 36).expires_at).toBe('2026-10-09T20:00:00Z');
    const limitExit = order({ order_id: 'tp', side: 'sell', limit_price: '12' });
    expect(exitReplacement(limitExit, 12.5, 0.01, 36)).toMatchObject({ limit_price: '12.5', stop_price: null });
  });

  it('explains server refusals', () => {
    expect(moveErrorMessage(new Error('Paper Trading request failed (409): {"code":"paper_risk_rejected","reason_codes":["STOP_NOT_BELOW_ENTRY"]}'))).toMatch(/stop loss/);
    expect(moveErrorMessage(new Error('(409): paper_order_not_open'))).toMatch(/no longer working/);
    expect(moveErrorMessage(new Error('(409): paper_order_replacement_requires_server_risk_authority'))).toMatch(/add exposure/);
  });
});
