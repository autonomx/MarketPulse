import type { PaperOrder, PaperOrderType, PaperTimeInForce } from './paperTypes';

// Order types and time in force for the paper ticket and order tables (TVP-7.1).

const orderTypeLabels: Record<PaperOrderType, string> = {
  market: 'Market',
  limit: 'Limit',
  stop: 'Stop',
  stop_limit: 'Stop limit',
  trailing_stop: 'Trailing stop',
};

export const timeInForceLabels: Record<PaperTimeInForce, string> = {
  gtc: 'GTC',
  day: 'DAY',
  gtd: 'GTD',
};

export function paperOrderTypeLabel(type: PaperOrderType): string {
  return orderTypeLabels[type] ?? type;
}

/**
 * Order types the ticket offers.
 *
 * Replay keeps the original three: replay snapshots carry no trigger or
 * trailing state. Risk-sized entries (buys) can be stop-limit orders; a
 * trailing stop is an exit (sell) here, and on entries it is the stop-loss
 * leg's "Trailing" option instead.
 */
export function ticketOrderTypes({ replay, entry }: { replay: boolean; entry: boolean }): PaperOrderType[] {
  if (replay) return ['market', 'limit', 'stop'];
  if (entry) return ['market', 'limit', 'stop', 'stop_limit'];
  return ['market', 'limit', 'stop', 'stop_limit', 'trailing_stop'];
}

/** The ISO timestamp for a `datetime-local` value, read in the browser's time zone. */
export function localDateTimeToIso(value: string): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

/** A short description of an order's time in force and trailing state for tables. */
export function paperOrderTerms(order: Pick<PaperOrder, 'order_type' | 'time_in_force' | 'expires_at' | 'trail_amount' | 'trail_percent'>): string {
  const timeInForce = order.time_in_force ?? 'gtc';
  const parts: string[] = [timeInForceLabels[timeInForce] ?? timeInForce];
  if (order.order_type === 'trailing_stop') {
    parts.push(order.trail_percent ? `trail ${Number(order.trail_percent)}%` : `trail ${Number(order.trail_amount ?? 0)}`);
  }
  return parts.join(' · ');
}
