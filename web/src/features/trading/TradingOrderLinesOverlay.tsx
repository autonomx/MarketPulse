import { useCallback, useEffect, useRef, useState } from 'react';
import type { TradingChartAdapter } from './chart/chartAdapter';
import { ChartPriceHandle } from './chart/ChartPriceHandle';
import { chartOrderLines, entryMoveInput, exitReplacement, moveErrorMessage, roundPrice, type ChartOrderLine } from './paperOrderLines';
import type { PaperOrder, PaperPositionProtection } from './paperTypes';
import { tradingPaperApi } from './tradingPaperApi';
import { emitOmnixEvent, PAPER_POSITION_PROTECTION_CHANGED_EVENT } from '../../events/bus';
import { POLL_INTERVALS_MS, startPolling } from '../../shared/timers';
import './TradingOrderLinesOverlay.css';

type Draft = { orderId: string; price: number; dragging: boolean };

function priceText(value: number): string {
  const digits = Math.abs(value) >= 1_000 ? 2 : Math.abs(value) >= 1 ? 4 : 6;
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

/** Re-renders when the chart's price-to-pixel mapping may have changed. */
function useChartViewport(adapter: TradingChartAdapter | null): void {
  const [, setRevision] = useState(0);
  useEffect(() => {
    if (!adapter) return;
    let frame: number | null = null;
    const invalidate = () => {
      if (frame !== null) return;
      frame = window.requestAnimationFrame(() => {
        frame = null;
        setRevision((value) => value + 1);
      });
    };
    const stops = [adapter.onVisibleRange(invalidate), adapter.onViewportChange(invalidate), adapter.onCrosshair(invalidate)];
    return () => {
      stops.forEach((stop) => stop());
      if (frame !== null) window.cancelAnimationFrame(frame);
    };
  }, [adapter]);
}

/**
 * Working paper orders on the chart (TVP-7.3): a line per order with its side, type and quantity; drag it to a new
 * price and confirm to move it, or cancel it from the line. Live paper only; replay orders stay in the replay panel.
 */
export function TradingOrderLinesOverlay({
  adapter, accountId, instrumentId, tickSize, disabled = false,
}: {
  adapter: TradingChartAdapter | null;
  accountId: string | null | undefined;
  instrumentId: string;
  tickSize: number | null;
  /** Replay: live orders are not drawn. */
  disabled?: boolean;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [orders, setOrders] = useState<PaperOrder[]>([]);
  const [protections, setProtections] = useState<PaperPositionProtection[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useChartViewport(adapter);

  const refresh = useCallback(async () => {
    if (!accountId) return;
    try {
      const [snapshot, nextProtections] = await Promise.all([tradingPaperApi.snapshot(accountId), tradingPaperApi.protections(accountId)]);
      setOrders(snapshot.open_orders);
      setProtections(nextProtections);
    } catch {
      // Keep the last lines; the next poll reconciles.
    }
  }, [accountId]);

  useEffect(() => {
    setOrders([]);
    setProtections([]);
    setDraft(null);
    setError(null);
    if (!accountId || disabled) return;
    void refresh();
    return startPolling(refresh, POLL_INTERVALS_MS.paperAccount);
  }, [accountId, disabled, instrumentId, refresh]);

  if (!adapter || !accountId || disabled) return null;
  const lines = chartOrderLines(orders, instrumentId, protections);
  if (lines.length === 0 && !error) return null;

  const priceAt = (clientY: number): number | null => {
    const bounds = rootRef.current?.getBoundingClientRect();
    if (!bounds) return null;
    const price = adapter.priceFromCoordinate(clientY - bounds.top);
    return price !== null && Number.isFinite(price) && price > 0 ? roundPrice(price, tickSize) : null;
  };

  const run = async (orderId: string, action: () => Promise<unknown>, failure: (error: unknown) => string) => {
    setBusy(orderId);
    setError(null);
    try {
      await action();
      setDraft(null);
      emitOmnixEvent(PAPER_POSITION_PROTECTION_CHANGED_EVENT, { accountId, instrumentId });
    } catch (caught) {
      setError(failure(caught));
    } finally {
      setBusy(null);
      await refresh();
    }
  };

  const confirmMove = (line: ChartOrderLine, price: number) => run(line.order.order_id, () => (line.move === 'entry'
    ? tradingPaperApi.moveRiskEntry(accountId, line.order.order_id, entryMoveInput(line.order, price, tickSize))
    : tradingPaperApi.replaceOrder(accountId, line.order.order_id, exitReplacement(line.order, price, tickSize))), moveErrorMessage);
  const cancel = (line: ChartOrderLine) => run(line.order.order_id, () => tradingPaperApi.cancelOrder(accountId, line.order.order_id), () => 'The order could not be cancelled.');

  return (
    <div ref={rootRef} className="trading-order-lines" role="group" aria-label={`${instrumentId} working orders`}>
      {lines.map((line) => {
        const moving = draft?.orderId === line.order.order_id ? draft : null;
        const price = moving?.price ?? line.price;
        const y = adapter.priceToCoordinate(price);
        if (y === null) return null;
        const working = busy === line.order.order_id;
        return (
          <ChartPriceHandle
            key={line.order.order_id}
            y={y}
            tone={line.order.side === 'buy' ? 'buy' : 'sell'}
            label={<>{line.label} <b>{priceText(price)}</b></>}
            ariaLabel={`${line.label} at ${priceText(price)}: drag to move`}
            priceAt={priceAt}
            dragging={moving?.dragging ?? false}
            onDrag={line.move && !working ? (next) => setDraft({ orderId: line.order.order_id, price: next, dragging: true }) : undefined}
            onDrop={(next) => setDraft({ orderId: line.order.order_id, price: next, dragging: false })}
          >
            {moving && !moving.dragging ? (
              <>
                <button type="button" disabled={working} onClick={() => void confirmMove(line, moving.price)} aria-label={`Move ${line.label} to ${priceText(moving.price)}`}>Move</button>
                <button type="button" disabled={working} onClick={() => setDraft(null)} aria-label="Keep the order where it was">Keep</button>
              </>
            ) : (
              <button type="button" disabled={working} onClick={() => void cancel(line)} aria-label={`Cancel ${line.label}`} title="Cancel order">×</button>
            )}
          </ChartPriceHandle>
        );
      })}
      {error ? <div className="trading-order-lines-error" role="alert">{error}<button type="button" onClick={() => setError(null)} aria-label="Dismiss">×</button></div> : null}
    </div>
  );
}
