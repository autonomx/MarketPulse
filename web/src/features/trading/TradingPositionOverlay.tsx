import { useEffect, useMemo, useRef, useState } from 'react';
import type { TradingChartAdapter } from './chart/chartAdapter';
import type { PaperOrder, PaperPosition, PaperSide } from './paperTypes';
import { tradingPaperApi } from './tradingPaperApi';
import { useTradingReplayStore } from './tradingReplayStore';
import { useTradingStore } from './tradingStore';
import { readPaperPositionProtection, writePaperPositionProtection, type PositionProtectionLevels } from './paperPositionProtection';
import './TradingPositionOverlay.css';
import { PAPER_POSITION_PROTECTION_CHANGED_EVENT } from '../../events/bus';
import { POLL_INTERVALS_MS, startPolling } from '../../shared/timers';
import { chartPalette } from './chartPalette';

type ProtectionLevel = 'takeProfit' | 'stopLoss';
type DraftProtection = { level: ProtectionLevel; value: number | null; dragging: boolean };
type OverlayPosition = PaperPosition & { pending?: boolean; pendingSide?: 'buy' | 'sell' };
type PositionAction = 'close' | 'reverse';

function priceLabel(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—';
  const digits = Math.abs(value) >= 1_000 ? 2 : Math.abs(value) >= 1 ? 4 : 6;
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function signedMoney(value: number): string {
  if (!Number.isFinite(value)) return '—';
  return `${value > 0 ? '+' : ''}${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD`;
}

function positionValue(position: PaperPosition, level: number | null): number {
  if (level === null) return 0;
  return (level - Number(position.average_cost)) * Number(position.quantity);
}

function displayMarket(instrumentId: string): string {
  const parts = instrumentId.split(':');
  const venue = parts[1] ?? 'Market';
  const rawSymbol = parts.at(-1) ?? instrumentId;
  return `${venue}:${rawSymbol.replaceAll('-', '')}`;
}

export function TradingPositionOverlay({
  adapter,
  accountId,
  instrumentId,
}: {
  adapter: TradingChartAdapter | null;
  accountId: string | null | undefined;
  instrumentId: string;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [position, setPosition] = useState<OverlayPosition | null>(null);
  const [protection, setProtection] = useState<PositionProtectionLevels>({ takeProfit: null, stopLoss: null });
  const [draft, setDraft] = useState<DraftProtection | null>(null);
  const [action, setAction] = useState<PositionAction | null>(null);
  const [actionStatus, setActionStatus] = useState<'idle' | 'saving'>('idle');
  const [actionError, setActionError] = useState<string | null>(null);
  const [partialClose, setPartialClose] = useState(false);
  const [actionQuantity, setActionQuantity] = useState('');
  const [viewport, setViewport] = useState({ width: 0, height: 0, revision: 0 });
  const replayMode = useTradingStore((state) => state.replayMode);
  const replayBar = useTradingReplayStore((state) => state.bar);
  const replaySnapshot = useTradingReplayStore((state) => state.snapshot);

  useEffect(() => {
    setProtection(replayMode ? { takeProfit: null, stopLoss: null } : accountId ? readPaperPositionProtection(accountId, instrumentId) : { takeProfit: null, stopLoss: null });
    setDraft(null);
    setAction(null);
    setActionError(null);
    if (replayMode) {
      const filledPosition = replaySnapshot?.positions.find((item) => item.instrument_id === instrumentId && Number(item.quantity) !== 0);
      const workingOrder = replaySnapshot?.open_orders.find((item) => item.instrument_id === instrumentId && Number(item.quantity) > 0);
      setPosition(filledPosition ?? (workingOrder ? {
        instrument_id: workingOrder.instrument_id,
        quantity: workingOrder.quantity,
        average_cost: workingOrder.reference_price ?? workingOrder.limit_price ?? workingOrder.stop_price ?? '',
        realized_pnl: '0',
        reserved_quantity: '0',
        last_price: workingOrder.reference_price ?? workingOrder.limit_price ?? workingOrder.stop_price ?? null,
        unrealized_pnl: '0',
        pending: true,
        pendingSide: workingOrder.side,
      } : null));
      return;
    }
    if (!accountId) {
      setPosition(null);
      return;
    }
    let cancelled = false;
    const refresh = async () => {
      try {
        const snapshot = await tradingPaperApi.snapshot(accountId);
        if (!cancelled) {
          const filledPosition = snapshot.positions.find((item) => item.instrument_id === instrumentId && Number(item.quantity) !== 0);
          const workingOrder = snapshot.open_orders.find((item) => item.instrument_id === instrumentId && Number(item.quantity) > 0);
          setPosition(filledPosition ?? (workingOrder ? {
            instrument_id: workingOrder.instrument_id,
            quantity: workingOrder.quantity,
            average_cost: workingOrder.reference_price ?? workingOrder.limit_price ?? workingOrder.stop_price ?? '',
            realized_pnl: '0',
            reserved_quantity: '0',
            last_price: workingOrder.reference_price ?? workingOrder.limit_price ?? workingOrder.stop_price ?? null,
            unrealized_pnl: '0',
            pending: true,
            pendingSide: workingOrder.side,
          } : null));
          setProtection(readPaperPositionProtection(accountId, instrumentId));
        }
      } catch {
        if (!cancelled) setPosition(null);
      }
    };
    void refresh();
    const stopPolling = startPolling(refresh, POLL_INTERVALS_MS.paperAccount);
    const changed = (event: Event) => {
      const detail = (event as CustomEvent<{ accountId?: string; instrumentId?: string }>).detail;
      if (detail?.accountId === accountId && detail.instrumentId === instrumentId) {
        setProtection(readPaperPositionProtection(accountId, instrumentId));
        void refresh();
      }
    };
    window.addEventListener(PAPER_POSITION_PROTECTION_CHANGED_EVENT, changed);
    return () => {
      cancelled = true;
      stopPolling();
      window.removeEventListener(PAPER_POSITION_PROTECTION_CHANGED_EVENT, changed);
    };
  }, [accountId, instrumentId, replayMode, replaySnapshot]);

  useEffect(() => {
    if (!adapter) return;
    let frame: number | null = null;
    const invalidate = () => {
      if (frame !== null) return;
      frame = window.requestAnimationFrame(() => {
        frame = null;
        setViewport((value) => ({ ...value, revision: value.revision + 1 }));
      });
    };
    const visibleRange = adapter.onVisibleRange(invalidate);
    const crosshair = adapter.onCrosshair(invalidate);
    const resize = new ResizeObserver((entries) => {
      const bounds = entries[0]?.contentRect;
      if (!bounds) return;
      setViewport((value) => ({ width: bounds.width, height: bounds.height, revision: value.revision + 1 }));
    });
    if (rootRef.current) resize.observe(rootRef.current);
    return () => {
      visibleRange();
      crosshair();
      resize.disconnect();
      if (frame !== null) window.cancelAnimationFrame(frame);
    };
  }, [adapter]);

  const entryPrice = position ? Number(position.average_cost) : null;
  const entryY = adapter && entryPrice !== null ? adapter.priceToCoordinate(entryPrice) : null;
  const currentProtection = useMemo<PositionProtectionLevels>(() => {
    if (!draft) return protection;
    return { ...protection, [draft.level]: draft.value };
  }, [draft, protection]);
  const takeProfitY = adapter && currentProtection.takeProfit !== null ? adapter.priceToCoordinate(currentProtection.takeProfit) : null;
  const stopLossY = adapter && currentProtection.stopLoss !== null ? adapter.priceToCoordinate(currentProtection.stopLoss) : null;
  const unrealized = Number(position?.unrealized_pnl ?? 0);
  const quantity = Math.abs(Number(position?.quantity ?? 0));
  const isShort = Number(position?.quantity ?? 0) < 0;
  const positionSide: PaperSide = isShort ? 'buy' : 'sell';
  const positionSideLabel = position?.pending ? 'Working' : isShort ? 'Short' : 'Long';

  const openAction = (nextAction: PositionAction) => {
    if (!position || position.pending || quantity <= 0) return;
    if (nextAction === 'reverse' && !isShort) {
      setActionError('Reverse is not available yet: close the position, then place the opposite order.');
      return;
    }
    setActionQuantity(String(quantity));
    setPartialClose(false);
    setActionError(null);
    setAction(nextAction);
  };

  const submitMarketOrder = async (side: PaperSide, orderQuantity: number, label: string): Promise<PaperOrder> => {
    if ((!accountId && !replayMode) || !position || !Number.isFinite(orderQuantity) || orderQuantity <= 0) {
      throw new Error('Enter a valid position quantity.');
    }
    const referencePrice = Number(position.last_price ?? position.average_cost);
    if (!Number.isFinite(referencePrice) || referencePrice <= 0) {
      throw new Error('A current paper price is required to close this position.');
    }
    const orderId = `paper-overlay-${label}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    if (replayMode) {
      if (!replaySnapshot || !replayBar) throw new Error('Select a replay bar before trading.');
      const result = await useTradingReplayStore.getState().placeOrder({
        order_id: orderId,
        instrument_id: instrumentId,
        binding_id: null,
        side,
        order_type: 'market',
        quantity: String(orderQuantity),
        limit_price: null,
        stop_price: null,
        reference_price: String(referencePrice),
        idempotency_key: orderId,
      });
      if (result.order.status === 'rejected') throw new Error(result.order.rejection_reason ?? 'Replay order rejected.');
      return result.order;
    }
    return tradingPaperApi.placeOrder(accountId!, {
      order_id: orderId,
      instrument_id: instrumentId,
      binding_id: null,
      side,
      order_type: 'market',
      quantity: String(orderQuantity),
      limit_price: null,
      stop_price: null,
      reference_price: String(referencePrice),
      idempotency_key: orderId,
    });
  };

  const waitForTerminalOrder = async (orderId: string): Promise<{ order: PaperOrder; snapshot: Awaited<ReturnType<typeof tradingPaperApi.snapshot>> }> => {
    if (!accountId) throw new Error('Paper account is unavailable.');
    const deadline = Date.now() + 15_000;
    while (Date.now() < deadline) {
      const snapshot = await tradingPaperApi.snapshot(accountId);
      const order = (snapshot.order_history ?? []).find((item) => item.order_id === orderId);
      if (order?.status === 'filled' || order?.status === 'rejected' || order?.status === 'cancelled') {
        return { order, snapshot };
      }
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
    throw new Error('Close order is still awaiting server execution. Protection remains active.');
  };

  const confirmAction = async () => {
    if (!action || !position || (!accountId && !replayMode)) return;
    if (action === 'reverse') {
      setActionError('Reverse is not available yet: close the position, then place the opposite order.');
      return;
    }
    const closeQuantity = partialClose ? Number(actionQuantity) : quantity;
    if (!Number.isFinite(closeQuantity) || closeQuantity <= 0 || closeQuantity > quantity) {
      setActionError('Enter a partial quantity no greater than the open position.');
      return;
    }
    setActionStatus('saving');
    setActionError(null);
    try {
      const submitted = await submitMarketOrder(positionSide, closeQuantity, 'close');
      let nextSnapshot = replayMode ? useTradingReplayStore.getState().snapshot : null;
      if (!replayMode) {
        const terminal = submitted.status === 'filled'
          ? { order: submitted, snapshot: await tradingPaperApi.snapshot(accountId!) }
          : await waitForTerminalOrder(submitted.order_id);
        if (terminal.order.status !== 'filled') {
          throw new Error(`Close order ${terminal.order.status}. Protection remains active.`);
        }
        nextSnapshot = terminal.snapshot;
        const remaining = terminal.snapshot.positions.find((item) => item.instrument_id === instrumentId && Number(item.quantity) !== 0);
        if (!remaining) {
          await tradingPaperApi.clearProtection(accountId!, instrumentId).catch(() => null);
          writePaperPositionProtection(accountId!, instrumentId, { takeProfit: null, stopLoss: null });
        }
      }
      const nextPosition = nextSnapshot?.positions.find((item) => item.instrument_id === instrumentId && Number(item.quantity) !== 0);
      setPosition(nextPosition ?? null);
      setAction(null);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Position action failed.');
    } finally {
      setActionStatus('idle');
    }
  };

  const priceFromClient = (clientY: number): number | null => {
    if (!rootRef.current || !adapter) return null;
    const bounds = rootRef.current.getBoundingClientRect();
    const value = adapter.priceFromCoordinate(clientY - bounds.top);
    return value !== null && Number.isFinite(value) && value > 0 ? value : null;
  };

  const startDrag = (level: ProtectionLevel) => (event: React.PointerEvent<HTMLElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (!adapter || !position) return;
    const start = priceFromClient(event.clientY) ?? entryPrice;
    if (start === null) return;
    setDraft({ level, value: start, dragging: true });
    const move = (pointer: PointerEvent) => {
      const next = priceFromClient(pointer.clientY);
      if (next !== null) setDraft((value) => value ? { ...value, value: next, dragging: true } : value);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
      setDraft((value) => value ? { ...value, dragging: false } : value);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
  };

  const confirmDraft = () => {
    if (!accountId || !draft || draft.value === null) return;
    const next = { ...protection, [draft.level]: draft.value };
    setProtection(next);
    setDraft(null);
    if (!replayMode) writePaperPositionProtection(accountId, instrumentId, next);
  };

  const discardDraft = () => setDraft(null);

  if (!position || entryY === null) return <div ref={rootRef} className="trading-position-overlay" aria-hidden="true" />;
  void viewport.revision;

  const zone = (levelY: number | null, color: string) => {
    if (levelY === null || viewport.height <= 0) return null;
    const top = Math.max(0, Math.min(viewport.height, Math.min(levelY, entryY)));
    const bottom = Math.max(0, Math.min(viewport.height, Math.max(levelY, entryY)));
    return <div className="trading-position-zone" style={{ top, height: Math.max(0, bottom - top), background: color }} />;
  };

  const levelVisual = (level: ProtectionLevel, value: number | null, y: number | null, color: string, label: string) => {
    if (value === null || y === null) return null;
    const dollars = positionValue(position, value);
    return (
      <div
        className={`trading-position-level trading-position-level-${level === 'takeProfit' ? 'tp' : 'sl'}${draft?.level === level && draft.dragging ? ' is-dragging' : ''}`}
        style={{ top: y, borderColor: color }}
        role="button"
        tabIndex={0}
        aria-label={`Drag to set ${label === 'TP' ? 'take profit' : 'stop loss'} at ${priceLabel(value)}`}
        title={`Drag to move ${label === 'TP' ? 'take profit' : 'stop loss'}`}
        onPointerDown={startDrag(level)}
      >
        <span className="trading-position-level-handle" style={{ borderColor: color, background: color }} aria-hidden="true" />
        <span className="trading-position-level-label" style={{ color }}>{label} <b>{priceLabel(value)}</b></span>
        <span className="trading-position-level-pnl" style={{ color }}>{signedMoney(dollars)}</span>
      </div>
    );
  };

  return (
    <div role="group" ref={rootRef} className={`trading-position-overlay${draft?.dragging ? ' is-dragging' : ''}`} aria-label={`${instrumentId} paper position`}>
      {draft ? zone(takeProfitY, chartPalette.tealWash) : null}
      {draft ? zone(stopLossY, chartPalette.amberWash) : null}
      {levelVisual('takeProfit', currentProtection.takeProfit, takeProfitY, chartPalette.teal, 'TP')}
      {levelVisual('stopLoss', currentProtection.stopLoss, stopLossY, chartPalette.amber, 'SL')}
      {position.pending && !replayMode ? null /* a working order's own line marks its price (TVP-7.3) */ : (
        <div className="trading-position-entry-line" style={{ top: entryY }}>
          <span className="trading-position-entry-price">{priceLabel(entryPrice)}</span>
        </div>
      )}
      <div className="trading-position-controls" style={{ top: entryY }} onPointerDown={(event) => event.stopPropagation()}>
        <button type="button" className="trading-position-direction" aria-label="Reverse paper position" title="Reverse requires short-position support" disabled={position.pending || actionStatus === 'saving' || !isShort} onClick={() => openAction('reverse')}>↕</button>
        {position.pending ? <span className="trading-position-working">Working</span> : null}
        <button type="button" className={`trading-position-protection trading-position-tp${currentProtection.takeProfit !== null ? ' is-set' : ''}`} title="Drag to add Take profit" aria-label="Drag to add Take profit" onPointerDown={startDrag('takeProfit')}>TP{currentProtection.takeProfit !== null ? ` ${priceLabel(currentProtection.takeProfit)}` : ''}</button>
        <button type="button" className={`trading-position-protection trading-position-sl${currentProtection.stopLoss !== null ? ' is-set' : ''}`} title="Drag to add Stop loss" aria-label="Drag to add Stop loss" onPointerDown={startDrag('stopLoss')}>SL{currentProtection.stopLoss !== null ? ` ${priceLabel(currentProtection.stopLoss)}` : ''}</button>
        <strong className="trading-position-quantity">{quantity.toLocaleString(undefined, { maximumFractionDigits: 6 })}</strong>
        <span className={`trading-position-pnl${unrealized < 0 ? ' is-negative' : ''}`}>{signedMoney(unrealized)}</span>
        <button type="button" className="trading-position-close" aria-label="Close paper position" title="Close position" disabled={position.pending || actionStatus === 'saving'} onClick={() => openAction('close')}>×</button>
        {draft ? <span className="trading-position-edit-actions"><button type="button" onClick={discardDraft}>Discard</button><button type="button" onClick={confirmDraft}>Confirm</button></span> : null}
      </div>
      {actionError && !action ? <div className="trading-position-action-error" role="alert">{actionError}</div> : null}
      {action ? (
        <div className="trading-position-action-backdrop" role="presentation">
          <section className="trading-position-action-dialog" role="dialog" aria-modal="true" aria-labelledby="trading-position-action-title">
            <header>
              <h2 id="trading-position-action-title">{action === 'close' ? 'Close position' : `Reverse ${displayMarket(instrumentId)} position?`}</h2>
              <button type="button" aria-label="Close position dialog" onClick={() => setAction(null)} disabled={actionStatus === 'saving'}>×</button>
            </header>
            {action === 'close' ? (
              <>
                <div className="trading-position-action-summary"><span className="trading-position-action-icon" aria-hidden="true">≋</span><strong>{displayMarket(instrumentId)}</strong><span>• {positionSideLabel} {quantity} @ {priceLabel(entryPrice)}</span></div>
                <label className="trading-position-partial"><input type="checkbox" checked={partialClose} onChange={(event) => setPartialClose(event.target.checked)} /> Partial close</label>
                {partialClose ? <label className="trading-position-partial-quantity">Quantity<input aria-label="Partial close quantity" inputMode="decimal" value={actionQuantity} onChange={(event) => setActionQuantity(event.target.value)} /></label> : null}
              </>
            ) : <p>Reverse is not available yet: close the position, then place the opposite order.</p>}
            {actionError ? <div className="trading-position-action-error" role="alert">{actionError}</div> : null}
            <footer><button type="button" onClick={() => setAction(null)} disabled={actionStatus === 'saving'}>Cancel</button><button type="button" className="primary" onClick={() => void confirmAction()} disabled={actionStatus === 'saving' || action === 'reverse'}>{actionStatus === 'saving' ? 'Saving…' : action === 'close' ? 'Close position' : 'Reverse unavailable'}</button></footer>
          </section>
        </div>
      ) : null}
    </div>
  );
}