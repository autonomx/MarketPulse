import { useEffect, useRef, type ReactNode } from 'react';
import './ChartPriceHandle.css';

/**
 * A draggable price line with a label (TVP-7.3's chart handle): working orders
 * use it, and anything else that is a price on the chart can. It reports the
 * price under the pointer while dragging and where it was dropped; what a
 * drop does is the caller's.
 */
export function ChartPriceHandle({
  y, tone, label, ariaLabel, priceAt, onDrag, onDrop, dragging = false, children,
}: {
  y: number;
  /** The line's colour family. */
  tone: 'buy' | 'sell';
  label: ReactNode;
  ariaLabel: string;
  /** The price at a client y, or null outside the chart. */
  priceAt: (clientY: number) => number | null;
  /** Omitted: the line can't be dragged. */
  onDrag?: (price: number) => void;
  onDrop?: (price: number) => void;
  dragging?: boolean;
  /** Controls after the label, such as cancel. */
  children?: ReactNode;
}) {
  const draggable = Boolean(onDrag);
  // Ends a drag in progress; also run when the line goes away mid-drag (its order filled).
  const stopDrag = useRef<(() => void) | null>(null);
  useEffect(() => () => stopDrag.current?.(), []);
  const start = (event: React.PointerEvent<HTMLElement>) => {
    if (!onDrag || event.button > 0) return;
    event.preventDefault();
    event.stopPropagation();
    stopDrag.current?.();
    let last: number | null = null;
    const move = (pointer: PointerEvent) => {
      const price = priceAt(pointer.clientY);
      if (price === null) return;
      last = price;
      onDrag(price);
    };
    const detach = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', end);
      window.removeEventListener('blur', detach);
      stopDrag.current = null;
    };
    const end = (pointer: PointerEvent) => {
      detach();
      if (pointer.type === 'pointerup' && last !== null) onDrop?.(last);
    };
    stopDrag.current = detach;
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
    // A release outside the window may never arrive: leaving the window ends the drag without a drop.
    window.addEventListener('blur', detach);
  };
  return (
    <div className={`chart-price-handle is-${tone}${draggable ? ' is-draggable' : ''}${dragging ? ' is-dragging' : ''}`} style={{ top: y }}>
      <div className="chart-price-handle-line" aria-hidden="true" onPointerDown={start} />
      <div className="chart-price-handle-label" onPointerDown={(event) => event.stopPropagation()}>
        <span
          className="chart-price-handle-grip"
          role={draggable ? 'button' : undefined}
          aria-label={draggable ? ariaLabel : undefined}
          tabIndex={draggable ? 0 : undefined}
          title={draggable ? 'Drag to move' : undefined}
          onPointerDown={start}
        >{label}</span>
        {children}
      </div>
    </div>
  );
}
