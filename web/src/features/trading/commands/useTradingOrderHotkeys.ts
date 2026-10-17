// Trading hotkeys (TVP-7.4): Shift+B / Shift+S buy or sell at market, and
// Shift+Alt+B / Shift+Alt+S buy or sell with a limit at the price under the
// crosshair (the last price when the crosshair is off the chart). Each fills
// the paper order ticket for the user to place: nothing is sent from a key, and
// the server's risk rules decide what the ticket may place.
import { useEffect, useRef } from 'react';
import type { TradingChartAdapter } from '../chart/chartAdapter';
import { paperTicketOpen, requestPaperTicket, type PaperTicketPrefill } from '../paperTicketRequests';
import { useTradingCommand } from './useTradingCommands';

export type OrderHotkeyModel = {
  active: boolean;
  adapter: TradingChartAdapter | null;
  instrumentId: string;
  /** The chart's last price, for a limit order when the crosshair is off the chart. */
  lastPrice: () => number | null;
};

/** The pre-fill a hotkey requests. */
export function hotkeyPrefill(instrumentId: string, side: 'buy' | 'sell', orderType: 'market' | 'limit', price: number | null): PaperTicketPrefill | null {
  if (orderType === 'limit' && !(price !== null && Number.isFinite(price) && price > 0)) return null;
  return { instrumentId, side, orderType, entry: orderType === 'limit' ? price : null, stop: null, target: null, quantity: null, source: 'hotkey' };
}

export function useTradingOrderHotkeys({ active, adapter, instrumentId, lastPrice }: OrderHotkeyModel): void {
  const crosshair = useRef<number | null>(null);
  useEffect(() => {
    if (!adapter) return undefined;
    return adapter.onCrosshair((point) => {
      crosshair.current = point && Number.isFinite(point.price) ? point.price : null;
    });
  }, [adapter]);

  const order = (side: 'buy' | 'sell', orderType: 'market' | 'limit') => () => {
    const prefill = hotkeyPrefill(instrumentId, side, orderType, crosshair.current ?? lastPrice());
    if (prefill) requestPaperTicket(prefill);
  };
  // Only with the paper ticket on screen, so Shift+letters keep typing symbols otherwise.
  const isActive = () => active && paperTicketOpen();
  useTradingCommand('trading.buyMarket', order('buy', 'market'), isActive);
  useTradingCommand('trading.sellMarket', order('sell', 'market'), isActive);
  useTradingCommand('trading.buyLimit', order('buy', 'limit'), isActive);
  useTradingCommand('trading.sellLimit', order('sell', 'limit'), isActive);
}
