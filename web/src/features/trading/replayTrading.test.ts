import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { PaperAccountSnapshot } from './paperTypes';
import { advanceReplaySnapshot, createReplaySnapshot, placeReplayOrder } from './replayTrading';
import type { MarketBar } from './tradingTypes';
import { fixture } from '../../test/fixture';

const replayApi = vi.hoisted(() => ({
  advanceExecution: vi.fn(),
  placeExecutionOrder: vi.fn(),
}));

vi.mock('./tradingReplayApi', () => ({ tradingReplayApi: replayApi }));

const bar = (close: string, high = close, low = close): MarketBar => ({
  instrument_id: 'equity:NYSE:TEST',
  interval: '1h',
  start_time: '2024-01-02T10:00:00Z',
  end_time: '2024-01-02T11:00:00Z',
  open: close,
  high,
  low,
  close,
  volume: '100',
  is_final: true,
  adjustment_mode: 'raw',
  session: '24x7',
  provider: 'replay-test',
  provider_event_id: null,
  provider_sequence: null,
  ingestion_revision: 1,
  received_at: '2024-01-02T11:00:01Z',
});

const snapshot = (): PaperAccountSnapshot => ({
  account: fixture({
    account_id: 'paper-1', name: 'Paper', base_currency: 'USD', commission_bps: '0',
    enabled: true, revision: 1,
  }),
  balances: [{ currency: 'USD', available: '1000', reserved: '25' }],
  margin_status: null,
  positions: [],
  open_orders: [],
  order_history: [fixture({
    account_id: 'paper-1', order_id: 'actual-order', instrument_id: 'equity:NYSE:TEST',
    side: 'buy', order_type: 'market', quantity: '1', status: 'filled', filled_quantity: '1',
    idempotency_key: 'actual-order',
  })],
  recent_fills: [],
  recent_ledger: [],
});

describe('replay trading', () => {
  beforeEach(() => vi.clearAllMocks());

  it('detaches replay cash and history without making a fill decision', () => {
    const source = snapshot();
    const replay = createReplaySnapshot(source);

    expect(replay.balances[0]).toMatchObject({ available: '1025', reserved: '0' });
    expect(replay.order_history).toEqual([]);
    expect(source.order_history).toHaveLength(1);
  });

  it('delegates market execution to the server kernel', async () => {
    const source = createReplaySnapshot(snapshot());
    const executed = {
      ...source,
      positions: [{
        instrument_id: 'equity:NYSE:TEST', quantity: '2', reserved_quantity: '0',
        average_cost: '101.101', realized_pnl: '0', last_price: '101.101', unrealized_pnl: '0',
      }],
    };
    replayApi.placeExecutionOrder.mockResolvedValue({
      snapshot: executed,
      order: {
        account_id: 'paper-1', order_id: 'replay-order-1', instrument_id: 'equity:NYSE:TEST',
        side: 'buy', order_type: 'market', quantity: '2', status: 'filled', filled_quantity: '2',
        average_fill_price: '101.101', reference_price: '101', idempotency_key: 'replay-order-1',
      },
    });

    const result = await placeReplayOrder(source, {
      order_id: 'replay-order-1', instrument_id: 'equity:NYSE:TEST', binding_id: null,
      side: 'buy', order_type: 'market', quantity: '2', limit_price: null, stop_price: null,
      reference_price: '101', idempotency_key: 'replay-order-1',
    }, bar('101'));

    expect(replayApi.placeExecutionOrder).toHaveBeenCalledWith(source, expect.any(Object), expect.objectContaining({ close: '101' }), true);
    expect(result.order.average_fill_price).toBe('101.101');
  });

  it('delegates bar advancement to the server kernel', async () => {
    const source = createReplaySnapshot(snapshot());
    replayApi.advanceExecution.mockResolvedValue(source);

    await expect(advanceReplaySnapshot(source, bar('92', '95', '89'))).resolves.toBe(source);
    expect(replayApi.advanceExecution).toHaveBeenCalledWith(source, expect.objectContaining({ low: '89' }));
  });
});
