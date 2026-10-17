// Test fixtures: working paper orders and a pending stop (TVP-7.3 tests).
import type { PaperOrder, PaperPositionProtection } from '../features/trading/paperTypes';

export const INSTRUMENT = 'equity:NYSE:TEST';

export function order(overrides: Partial<PaperOrder> = {}): PaperOrder {
  return {
    account_id: 'paper-1', order_id: 'entry-1', instrument_id: INSTRUMENT, binding_id: 'feed', side: 'buy', order_type: 'limit',
    quantity: '100', limit_price: '10', stop_price: null, reference_price: null, status: 'open', filled_quantity: '0',
    idempotency_key: 'entry-1', reserved_cash: '1000', time_in_force: 'day', expires_at: null, ...overrides,
  } as PaperOrder;
}

export const pendingStop: PaperPositionProtection = {
  account_id: 'paper-1', instrument_id: INSTRUMENT, entry_order_id: 'entry-1', stop_loss: '9', take_profit: '12', status: 'pending_entry',
} as PaperPositionProtection;
