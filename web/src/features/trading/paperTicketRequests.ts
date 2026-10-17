// Orders pre-filled from drawings (TVP-3.6): a long or short position's
// "Create buy/sell order" action sends an `order-ticket` request on the drawing
// action bus. The workspace opens the paper panel and leaves the pre-fill here;
// the panel takes it, fills the ticket and the user confirms. Nothing is placed
// without the panel's own confirmation.
import { useEffect, useRef } from 'react';
import { onDrawingActionRequest } from './drawings/drawingActions';

export type PaperTicketPrefill = {
  instrumentId: string;
  side: 'buy' | 'sell';
  /** A limit order at `entry` (position drawings, limit hotkeys), a stop order at `entry` (the chart's price-scale
   * menu, TVP-7.3) or a market order (TVP-7.4 hotkeys, the chart's buy/sell buttons). */
  orderType: 'market' | 'limit' | 'stop';
  /** The limit or stop price; null for a market order. */
  entry: number | null;
  stop: number | null;
  target: number | null;
  quantity: number | null;
  /** Where it came from, for the notice; a drawing's request when absent. */
  source?: 'drawing' | 'hotkey' | 'chart';
};

const positive = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null);

/** The request's payload as a pre-fill, or null when it is not one (the bus carries anything). */
export function parsePaperTicketRequest(payload: unknown): PaperTicketPrefill | null {
  if (!payload || typeof payload !== 'object') return null;
  const value = payload as Record<string, unknown>;
  const entry = positive(value.entry);
  const orderType = value.orderType === 'market' || value.orderType === 'stop' ? value.orderType : 'limit';
  if (typeof value.instrumentId !== 'string' || !value.instrumentId || (value.side !== 'buy' && value.side !== 'sell')) return null;
  if (orderType !== 'market' && entry === null) return null;
  return {
    instrumentId: value.instrumentId, side: value.side, orderType, entry: orderType === 'market' ? null : entry,
    stop: positive(value.stop), target: positive(value.target), quantity: positive(value.quantity),
  };
}

/** A pre-fill the panel doesn't take within this long is dropped: it never fills a ticket much later. */
export const PREFILL_LIFETIME_MS = 10_000;

let pending: { prefill: PaperTicketPrefill; at: number } | null = null;
const listeners = new Set<() => void>();

export function requestPaperTicket(prefill: PaperTicketPrefill, now = Date.now()): void {
  pending = { prefill, at: now };
  for (const listener of listeners) listener();
}

/** Takes the pending pre-fill (once), unless it is older than `PREFILL_LIFETIME_MS`. */
export function takePaperTicketPrefill(now = Date.now()): PaperTicketPrefill | null {
  const taken = pending;
  pending = null;
  return taken && now - taken.at <= PREFILL_LIFETIME_MS ? taken.prefill : null;
}

let openPanels = 0;

/** Whether a paper order ticket is on screen (the trading hotkeys act only then, TVP-7.4). */
export function paperTicketOpen(): boolean {
  return openPanels > 0;
}

/** The paper panel calls this while it is mounted. */
export function usePaperTicketPresence(): void {
  useEffect(() => {
    openPanels += 1;
    return () => {
      openPanels -= 1;
    };
  }, []);
}

/** The pending pre-fill's source, without taking it. */
function pendingSource(): PaperTicketPrefill['source'] {
  return pending?.prefill.source;
}

export function onPaperTicketRequest(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Workspace side: handles `order-ticket` requests by opening the paper panel with the pre-fill. */
export function usePaperTicketRequests(openPaperPanel: () => void): void {
  const open = useRef(openPaperPanel);
  useEffect(() => {
    open.current = openPaperPanel;
  });
  useEffect(() => onDrawingActionRequest('order-ticket', (payload) => {
    const prefill = parsePaperTicketRequest(payload);
    if (prefill) requestPaperTicket(prefill);
  }), []);
  // A drawing's request opens the paper panel; hotkeys only act while it is open, so they rearrange nothing.
  useEffect(() => onPaperTicketRequest(() => {
    if (pendingSource() !== 'hotkey') open.current();
  }), []);
}

/**
 * Whether an order is an entry the server sizes from its risk (and that needs a stop): every buy outside replay, and
 * a sell that opens a short, on an account that allows shorting, with no long position to reduce (TVP-7.2a).
 */
export function isRiskEntry(
  side: 'buy' | 'sell',
  account: { allow_short?: boolean } | null | undefined,
  position: { quantity: string | number } | null | undefined,
): boolean {
  if (side === 'buy') return true;
  return Boolean(account?.allow_short) && !(Number(position?.quantity ?? 0) > 0);
}

/** The ticket fields a pre-fill sets. */
export type PaperTicketForm = {
  setTicketTab: (tab: 'order') => void;
  setSide: (side: 'buy' | 'sell') => void;
  setOrderType: (type: 'limit' | 'market' | 'stop') => void;
  /** A limit or stop order's price (the panel's "Limit price" or "Stop price" field). */
  setTriggerPrice: (value: string) => void;
  /** Only a stop-limit's second price; cleared. */
  setLimitPrice: (value: string) => void;
  setStopLossEnabled: (enabled: boolean) => void;
  setStopLoss: (value: string) => void;
  setTakeProfitEnabled: (enabled: boolean) => void;
  setTakeProfit: (value: string) => void;
  setQuantity: (value: string) => void;
};

/** How the panel will place this order: a risk-managed entry carries stop and target and sizes the quantity itself. */
export type PaperTicketMode = { riskManaged: boolean; riskPercent: string; /** The open long position's quantity, if any. */ longQuantity?: number | null };

/**
 * Fills the ticket from a pre-fill: a limit order at the entry. A risk-managed entry (a buy outside replay) also
 * gets the stop and target, and the account's risk rule sizes it; any other order gets the drawing's quantity and
 * no protection, since the panel can't send it, and the notice says so. Returns the notice to show; a drawing on
 * another symbol fills nothing.
 */
export function applyPaperTicketPrefill(
  prefill: PaperTicketPrefill,
  instrumentId: string,
  form: PaperTicketForm,
  mode: PaperTicketMode,
  symbolOf: (instrumentId: string) => string = (id) => id,
): { kind: 'success' | 'error'; message: string } {
  if (prefill.instrumentId !== instrumentId) {
    return { kind: 'error', message: `The order is for ${symbolOf(prefill.instrumentId)}; open that chart to trade it.` };
  }
  form.setTicketTab('order');
  form.setSide(prefill.side);
  form.setOrderType(prefill.orderType);
  form.setTriggerPrice(prefill.entry === null ? '' : String(prefill.entry));
  form.setLimitPrice('');
  const protect = mode.riskManaged;
  form.setStopLossEnabled(protect && prefill.stop !== null);
  form.setStopLoss(protect && prefill.stop !== null ? String(prefill.stop) : '');
  form.setTakeProfitEnabled(protect && prefill.target !== null);
  form.setTakeProfit(protect && prefill.target !== null ? String(prefill.target) : '');
  // A sell's default quantity is the long position it closes (TradingView's default quantity for a reducing order).
  const quantity = prefill.quantity ?? (prefill.side === 'sell' ? mode.longQuantity ?? null : null);
  if (!protect && quantity !== null) form.setQuantity(String(Number(quantity.toPrecision(6))));
  const detail = !protect
    ? prefill.side === 'sell'
      ? 'A sell closes or reduces a long position; to open a short, turn shorting on in the account settings.'
      : 'This order can\'t carry a stop and target: add them after it fills.'
    : prefill.stop === null
      ? `Set a stop loss: the account's ${mode.riskPercent}% risk rule sizes the quantity from it.`
      : `The account's ${mode.riskPercent}% risk rule sizes the quantity.`;
  const from = prefill.source === 'hotkey' ? 'Ticket filled from the trading hotkey.'
    : prefill.source === 'chart' ? 'Ticket filled from the chart.' : 'Ticket filled from the position drawing.';
  return { kind: 'success', message: `${from} ${detail} Check it, then place the order.` };
}

/**
 * Panel side: calls `apply` with each pre-fill, also one left before the panel mounted. Until `ready` (the accounts
 * are loaded, so a sell knows whether it opens a short) pre-fills wait; they still expire after their lifetime.
 */
export function usePaperTicketPrefill(apply: (prefill: PaperTicketPrefill) => void, ready = true): void {
  // The ticket that takes pre-fills is on screen while this is mounted.
  usePaperTicketPresence();
  const handler = useRef(apply);
  useEffect(() => {
    handler.current = apply;
  });
  useEffect(() => {
    if (!ready) return;
    const take = () => {
      const prefill = takePaperTicketPrefill();
      if (prefill) handler.current(prefill);
    };
    take();
    return onPaperTicketRequest(take);
  }, [ready]);
}
