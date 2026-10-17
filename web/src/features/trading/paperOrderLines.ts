// Working orders drawn on the chart (TVP-7.3): which orders get a line, what
// the line says, and the request a drag sends. A dragged buy entry placed with
// server risk is re-priced by the server (it sizes the moved entry again); a
// sell (an exit) is replaced with the same order at the new price. Both go to
// the server's own validation; nothing here sizes or authorizes an order.
import { MOVED_ORDER_MARKER } from './paperNotifications';
import type { PaperOrder, PaperOrderInput, PaperPositionProtection, PaperRiskEntryMoveInput } from './paperTypes';

export type OrderLineMove = 'entry' | 'exit' | null;

export type ChartOrderLine = {
  order: PaperOrder;
  /** The price the line is drawn at: the limit, or the stop until a stop-limit's stop is reached. */
  price: number;
  /** e.g. "BUY LMT 200". */
  label: string;
  /** How a drag re-prices it; null when it can't be dragged (a trailing stop moves by itself). */
  move: OrderLineMove;
};

const TYPE_LABELS: Record<PaperOrder['order_type'], string> = {
  market: 'MKT', limit: 'LMT', stop: 'STP', stop_limit: 'STP LMT', trailing_stop: 'TRAIL',
};
const MOVABLE_TYPES = new Set<PaperOrder['order_type']>(['limit', 'stop', 'stop_limit']);

const num = (value: unknown): number | null => {
  const parsed = Number(value);
  return value !== null && value !== undefined && value !== '' && Number.isFinite(parsed) && parsed > 0 ? parsed : null;
};

function linePrice(order: PaperOrder): number | null {
  if (order.order_type === 'limit') return num(order.limit_price);
  if (order.order_type === 'stop_limit') return order.stop_triggered_at ? num(order.limit_price) : num(order.stop_price);
  if (order.order_type === 'stop' || order.order_type === 'trailing_stop') return num(order.stop_price);
  return null;
}

function remaining(order: PaperOrder): number {
  return Math.max(0, Number(order.quantity) - Number(order.filled_quantity ?? 0));
}

function lineMove(order: PaperOrder, protections: readonly PaperPositionProtection[]): OrderLineMove {
  if (!MOVABLE_TYPES.has(order.order_type) || (order.order_type === 'stop_limit' && order.stop_triggered_at)) return null;
  // A risk entry (a buy, or a short's sell, TVP-7.2a) carries a pending stop; any other sell is an exit.
  const armed = protections.some((item) => item.entry_order_id === order.order_id && item.status === 'pending_entry' && num(item.stop_loss) !== null);
  if (armed) return Number(order.filled_quantity ?? 0) === 0 ? 'entry' : null;
  return order.side === 'sell' ? 'exit' : null;
}

/** The chart's working orders for one instrument, as lines. */
export function chartOrderLines(
  orders: readonly PaperOrder[],
  instrumentId: string,
  protections: readonly PaperPositionProtection[],
): ChartOrderLine[] {
  return orders.flatMap((order) => {
    if (order.instrument_id !== instrumentId || order.status !== 'open' || remaining(order) <= 0) return [];
    const price = linePrice(order);
    if (price === null) return [];
    const quantity = Number(remaining(order).toPrecision(8));
    return [{ order, price, label: `${order.side.toUpperCase()} ${TYPE_LABELS[order.order_type]} ${quantity}`, move: lineMove(order, protections) }];
  });
}

/** A price on the instrument's tick (or to 8 significant digits without one). */
export function roundPrice(price: number, tickSize: number | null): number {
  if (tickSize && tickSize > 0) {
    const decimals = Math.max(0, Math.ceil(-Math.log10(tickSize)) + 1);
    return Number((Math.round(price / tickSize) * tickSize).toFixed(decimals));
  }
  return Number(price.toPrecision(8));
}

const moveId = (order: PaperOrder, now: number) => `${order.order_id.slice(0, 120)}${MOVED_ORDER_MARKER}${now.toString(36)}`;

/** A stop-limit keeps its limit's distance from the stop when the stop moves. */
function movedLimit(order: PaperOrder, price: number, tickSize: number | null): number | null {
  if (order.order_type !== 'stop_limit') return null;
  const stop = num(order.stop_price);
  const limit = num(order.limit_price);
  return stop !== null && limit !== null ? roundPrice(limit + (price - stop), tickSize) : null;
}

/** The server request that moves a risk entry to `price`. */
export function entryMoveInput(order: PaperOrder, price: number, tickSize: number | null, now = Date.now()): PaperRiskEntryMoveInput {
  const id = moveId(order, now);
  return { order_id: id, idempotency_key: id, trigger_price: String(price), limit_price: movedLimit(order, price, tickSize)?.toString() ?? null };
}

/** The replacement for an exit order moved to `price`: the same order for what is left of it. */
export function exitReplacement(order: PaperOrder, price: number, tickSize: number | null, now = Date.now()): PaperOrderInput {
  const id = moveId(order, now);
  const limit = order.order_type === 'limit' ? price : movedLimit(order, price, tickSize);
  return {
    order_id: id,
    idempotency_key: id,
    instrument_id: order.instrument_id,
    binding_id: order.binding_id ?? null,
    side: order.side,
    order_type: order.order_type,
    quantity: String(remaining(order)),
    limit_price: limit === null ? null : String(limit),
    stop_price: order.order_type === 'limit' ? null : String(price),
    reference_price: null,
    time_in_force: order.time_in_force,
    // A DAY order's stored expiry is derived by the server; only a GTD order sends one.
    expires_at: order.time_in_force === 'gtd' ? order.expires_at ?? null : null,
  };
}

/** Why a move failed, in words (the server's codes otherwise). */
export function moveErrorMessage(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  if (text.includes('STOP_NOT_BELOW_ENTRY')) return 'The entry can\'t move to or below its stop loss.';
  if (text.includes('paper_risk_rejected')) return 'The risk rules rejected the moved entry; it stays where it was.';
  if (text.includes('not_open')) return 'The order is no longer working (it filled or was cancelled).';
  if (text.includes('requires_server_risk_authority')) return 'This order can\'t be moved: it would add exposure. Cancel it and place a new one.';
  return 'The order could not be moved; it stays where it was.';
}
