// Paper trading notifications (TVP-7.4): fills, rejections, cancellations
// and expiries noticed between account snapshots, shown as a toast over the
// chart and kept in the terminal dock's Notifications tab. The first snapshot
// of an account only sets the baseline, so opening Omnix replays nothing.
// Orders placed by a margin call (TVP-7.2b) say so.
import { useEffect, useRef, useSyncExternalStore } from 'react';
import { isMarginCallOrder } from './paperAccountMargin';
import type { PaperAccountSnapshot, PaperOrder } from './paperTypes';

export type PaperNotificationKind = 'fill' | 'partial' | 'reject' | 'cancel' | 'modify' | 'expire' | 'closed';
export type PaperNotification = { id: string; at: string; kind: PaperNotificationKind; orderId: string; message: string };

const LOG_LIMIT = 200;
let log: readonly PaperNotification[] = [];
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

/** Adds notifications, newest first; ones already logged (same id) are not added again. */
export function pushPaperNotifications(items: readonly PaperNotification[]): void {
  const known = new Set(log.map((item) => item.id));
  const fresh = items.filter((item) => !known.has(item.id)).sort((left, right) => Date.parse(right.at) - Date.parse(left.at));
  if (fresh.length === 0) return;
  log = [...fresh, ...log].slice(0, LOG_LIMIT);
  emit();
}

export function clearPaperNotifications(): void {
  log = [];
  emit();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The notifications log, newest first. */
export function usePaperNotifications(): readonly PaperNotification[] {
  return useSyncExternalStore(subscribe, () => log, () => log);
}

function symbolOf(instrumentId: string): string {
  return instrumentId.split(':').at(-1)?.replace('-', '/') ?? instrumentId;
}

/** An order moved on the chart is replaced by one whose id extends it (`paperOrderLines.ts`). */
export const MOVED_ORDER_MARKER = '-moved-';

function movedPrice(order: PaperOrder, current: readonly PaperOrder[]): string | null {
  const replacement = current.find((item) => item.order_id.startsWith(`${order.order_id.slice(0, 120)}${MOVED_ORDER_MARKER}`));
  if (!replacement) return null;
  return String(replacement.order_type === 'limit' ? replacement.limit_price : replacement.stop_price ?? replacement.limit_price ?? '');
}

/** "Buy 2 BTC/USDT MARKET", with "Margin call:" in front of an order a margin call placed. */
function orderLabel(order: PaperOrder): string {
  const what = `${order.side === 'buy' ? 'Buy' : 'Sell'} ${order.quantity} ${symbolOf(order.instrument_id)} ${order.order_type.replace('_', ' ').toUpperCase()}`;
  return isMarginCallOrder(order.order_id) ? `Margin call: ${what}` : what;
}

function describe(order: PaperOrder, current: readonly PaperOrder[] = []): Omit<PaperNotification, 'id' | 'at' | 'orderId'> | null {
  const what = orderLabel(order);
  switch (order.status) {
    case 'filled':
      return { kind: 'fill', message: `${what} filled${order.average_fill_price ? ` at ${order.average_fill_price}` : ''}` };
    case 'rejected':
      return { kind: 'reject', message: `${what} rejected${order.rejection_reason ? `: ${order.rejection_reason.replace(/_/g, ' ')}` : ''}` };
    case 'cancelled': {
      const moved = movedPrice(order, current);
      return moved !== null ? { kind: 'modify', message: `${what} moved to ${moved}` } : { kind: 'cancel', message: `${what} cancelled` };
    }
    case 'expired':
      return { kind: 'expire', message: `${what} expired` };
    default:
      return null;
  }
}

function ordersOf(snapshot: PaperAccountSnapshot): PaperOrder[] {
  const byId = new Map<string, PaperOrder>();
  for (const order of [...(snapshot.order_history ?? []), ...(snapshot.open_orders ?? [])]) byId.set(order.order_id, order);
  return [...byId.values()];
}

/**
 * The notifications for what changed from `before` to `after`: orders whose status changed (or that appear already
 * closed), open orders that filled part of their quantity, and open orders that left the snapshot altogether (an old
 * order outside the history window that closed). Orders that keep their state notify nothing.
 */
export function orderNotifications(before: PaperAccountSnapshot, after: PaperAccountSnapshot, now = new Date().toISOString()): PaperNotification[] {
  const previous = new Map(ordersOf(before).map((order) => [order.order_id, order]));
  const current = ordersOf(after);
  const notes = current.flatMap((order): PaperNotification[] => {
    const prior = previous.get(order.order_id);
    if (order.status === 'open') {
      const filled = Number(order.filled_quantity ?? 0);
      if (filled > 0 && filled !== Number(prior?.filled_quantity ?? 0)) {
        const what = orderLabel(order);
        return [{ id: `${order.order_id}:partial:${filled}`, at: order.updated_at ?? now, orderId: order.order_id, kind: 'partial', message: `${what} partly filled: ${filled} of ${order.quantity}` }];
      }
      return [];
    }
    if (prior?.status === order.status) return [];
    const described = describe(order, current);
    return described ? [{ id: `${order.order_id}:${order.status}`, at: order.updated_at ?? now, orderId: order.order_id, ...described }] : [];
  });
  const present = new Set(current.map((order) => order.order_id));
  for (const order of before.open_orders ?? []) {
    if (present.has(order.order_id)) continue;
    const what = orderLabel(order);
    notes.push({ id: `${order.order_id}:closed`, at: now, orderId: order.order_id, kind: 'closed', message: `${what} is no longer open` });
  }
  return notes;
}

/**
 * Watches snapshots and logs what changed. `mode` names the order book being watched (`live`, or a replay session):
 * a new account or mode starts a new baseline, so switching between live and replay reports nothing.
 */
export function usePaperOrderNotifications(snapshot: PaperAccountSnapshot | null, mode = 'live'): void {
  const previous = useRef<{ key: string; snapshot: PaperAccountSnapshot } | null>(null);
  useEffect(() => {
    if (!snapshot) return;
    const key = `${snapshot.account.account_id}:${mode}`;
    const before = previous.current;
    previous.current = { key, snapshot };
    if (!before || before.key !== key) return;
    pushPaperNotifications(orderNotifications(before.snapshot, snapshot));
  }, [snapshot, mode]);
}
