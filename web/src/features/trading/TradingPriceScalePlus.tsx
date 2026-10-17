import { useEffect, useRef, useState } from 'react';
import type { TradingChartAdapter } from './chart/chartAdapter';
import { roundPrice } from './paperOrderLines';
import { requestPaperTicket, type PaperTicketPrefill } from './paperTicketRequests';
import './TradingPriceScalePlus.css';

type Hover = { price: number; y: number };
export type PriceScaleMenuItem = { label: string; side: 'buy' | 'sell'; orderType: 'limit' | 'stop' } | { label: string; alert: true };

/** TradingView's "+" menu at a price: below the market a buy limit or a sell stop, above it a sell limit or a buy stop. */
export function priceScaleMenuItems(price: number, last: number | null, text: string): PriceScaleMenuItem[] {
  const below = last === null || price <= last;
  const orders: PriceScaleMenuItem[] = below
    ? [{ label: `Buy limit at ${text}`, side: 'buy', orderType: 'limit' }, { label: `Sell stop at ${text}`, side: 'sell', orderType: 'stop' }]
    : [{ label: `Sell limit at ${text}`, side: 'sell', orderType: 'limit' }, { label: `Buy stop at ${text}`, side: 'buy', orderType: 'stop' }];
  return [...orders, { label: `Add alert at ${text}`, alert: true }];
}

const HIDE_DELAY_MS = 250;

/**
 * The "+" beside the price scale at the pointer's price (TVP-7.3). Its orders fill the paper ticket, where they are
 * checked and placed like any other; "Add alert" opens the alert dialog at that price.
 */
export function TradingPriceScalePlus({
  adapter, instrumentId, lastPrice, tickSize, onAddAlert,
}: {
  adapter: TradingChartAdapter | null;
  instrumentId: string;
  lastPrice: number | null;
  tickSize: number | null;
  onAddAlert: (price: number, y: number) => void;
}) {
  const [hover, setHover] = useState<Hover | null>(null);
  const [menu, setMenu] = useState<Hover | null>(null);
  const overButton = useRef(false);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!adapter) return;
    const stop = adapter.onCrosshair((point) => {
      if (hideTimer.current !== null) clearTimeout(hideTimer.current);
      hideTimer.current = null;
      const y = point?.pointer ? adapter.priceToCoordinate(point.price) : null;
      if (point && y !== null) {
        setHover({ price: roundPrice(point.price, tickSize), y });
        return;
      }
      // The pointer left the plot; moving onto the button itself also does that, so wait before hiding.
      hideTimer.current = setTimeout(() => {
        if (!overButton.current) setHover(null);
      }, HIDE_DELAY_MS);
    });
    return () => {
      stop();
      if (hideTimer.current !== null) clearTimeout(hideTimer.current);
    };
  }, [adapter, tickSize]);

  useEffect(() => {
    if (!menu) return;
    // A click anywhere else closes the menu (clicks inside stop at its root).
    const close = () => setMenu(null);
    window.addEventListener('pointerdown', close);
    return () => window.removeEventListener('pointerdown', close);
  }, [menu]);

  const box = adapter?.mainPriceScaleBox() ?? null;
  if (!adapter || !box) return null;
  const at = menu ?? hover;
  if (!at || at.y < 0 || at.y > box.paneHeight) return null;
  const text = at.price.toLocaleString(undefined, { maximumFractionDigits: 8 });
  const side = box.side === 'right' ? { right: box.scaleWidth + 2 } : { left: box.scaleWidth + 2 };

  const choose = (item: PriceScaleMenuItem) => {
    setMenu(null);
    if ('alert' in item) {
      onAddAlert(at.price, at.y);
      return;
    }
    const prefill: PaperTicketPrefill = {
      instrumentId, side: item.side, orderType: item.orderType, entry: at.price, stop: null, target: null, quantity: null, source: 'chart',
    };
    requestPaperTicket(prefill);
  };

  return (
    <div className="trading-price-scale-plus" style={{ top: at.y, ...side }} onPointerDown={(event) => event.stopPropagation()}>
      <button
        type="button"
        className="trading-price-scale-plus-button"
        aria-label={`Trade or add an alert at ${text}`}
        aria-haspopup="menu"
        aria-expanded={menu !== null}
        title={`Orders and alerts at ${text}`}
        onPointerEnter={() => { overButton.current = true; }}
        onPointerLeave={() => { overButton.current = false; }}
        onClick={() => setMenu(menu ? null : at)}
      >+</button>
      {menu ? (
        <div className="trading-price-scale-plus-menu" role="menu" aria-label={`Orders at ${text}`} onKeyDown={(event) => { if (event.key === 'Escape') setMenu(null); }}>
          {priceScaleMenuItems(at.price, lastPrice, text).map((item) => (
            <button key={item.label} type="button" role="menuitem" className={'alert' in item ? 'is-alert' : `is-${item.side}`} onClick={() => choose(item)}>{item.label}</button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
