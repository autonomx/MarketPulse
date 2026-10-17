/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { applyConditionDrafts, conditionEditorFields, isPriceLineAlert, withPriceLineCondition } from './alertConditionDrafts';
import { chartAlertIndicatorId, chartAlertSourceIndicatorId, chartAlertThreshold, conditionsAtValue, resolveIndicatorSelection, withChartIndicatorCondition, type AlertIndicatorChoice } from './alertIndicatorSources';
import { indicatorUsesSeparatePane, type CoreIndicatorId } from './indicators/coreIndicators';
import { tradingViewBuiltInUsesSeparatePane } from './indicators/tradingViewBuiltIns';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { TradingChartAdapter } from './chart/chartAdapter';
import { TradingAlertDialog, type TradingAlertEditorState } from './TradingAlertDialog';
import type { ChartAlertPlacement } from './drawings/TradingDrawingOverlay';
import {
  alertFrequency,
  alertLastTriggeredLabel,
  alertVisualState,
  chartAlertCreateInput,
  chartAlertUpdateInput,
  expirationTimestamp,
  formatAlertThreshold,
  notifyTradingAlertsChanged,
  type TradingChartAlertState,
} from './tradingChartAlerts';
import { deliveryCreateFields, deliveryEditorOf, deliveryUpdatePatch, defaultAlertNotifications } from './alertDelivery';
import { tradingApi } from './tradingApi';
import type { TradingAlert } from './tradingTypes';
import { useTradingAlertMutations, useTradingAlerts } from './useTradingAlerts';
import './TradingChartAlertOverlay.css';
import { chartPalette } from './chartPalette';

type DragState = { alert: TradingAlert; threshold: number };
type TrendlineMode = NonNullable<TradingAlert['parameters']['trendline_mode']>;

function indicatorIdForAlert(alert: TradingAlert): CoreIndicatorId | null {
  // Legacy indicator alerts and single "indicator line vs value" conditions alerts (TVP-1.3) are drawn on their pane.
  return chartAlertIndicatorId(alert) as CoreIndicatorId | null;
}

/** The indicator pane an alert sits on; null when its indicator is drawn on the price pane (it moves in prices). */
function paneIndicatorId(alert: TradingAlert): CoreIndicatorId | null {
  const indicatorId = indicatorIdForAlert(alert);
  const separate = (id: string) => indicatorUsesSeparatePane(id as CoreIndicatorId) || tradingViewBuiltInUsesSeparatePane(id);
  if (indicatorId && separate(indicatorId)) return indicatorId;
  // An overlay on a pane indicator (SMA of RSI, TVP-6.5) is drawn in that indicator's pane.
  const sourceId = indicatorId ? chartAlertSourceIndicatorId(alert) : null;
  return sourceId && separate(sourceId) ? sourceId as CoreIndicatorId : null;
}

/** The update that moves a dragged alert to `threshold`: its condition's value (unrounded) for a conditions alert. */
function thresholdUpdate(alert: TradingAlert, threshold: number) {
  if (alert.condition_type !== 'conditions') return chartAlertUpdateInput(alert, { threshold: formatAlertThreshold(threshold) });
  const input = chartAlertUpdateInput(alert, {});
  return { ...input, conditions: conditionsAtValue(alert, String(Number(threshold.toPrecision(8)))) as typeof input.conditions };
}

function trendlineModeForCondition(condition: string): TrendlineMode | null {
  if (condition === 'trendline_above') return 'greater_than';
  if (condition === 'trendline_below') return 'less_than';
  if (condition.startsWith('trendline_')) return condition.replace('trendline_', '') as TrendlineMode;
  return null;
}

function localDateTime(value?: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function isoDateTime(value: string): string | null {
  if (!value) return null;
  const milliseconds = Date.parse(value);
  return Number.isFinite(milliseconds) ? new Date(milliseconds).toISOString() : null;
}

function alertColor(state: TradingChartAlertState): string {
  if (state === 'triggered') return chartPalette.yellow;
  if (state === 'disabled') return chartPalette.slate;
  if (state === 'expired') return chartPalette.lavender;
  return chartPalette.alertOrange;
}

function formattedPrice(value: number): string {
  if (!Number.isFinite(value)) return '—';
  return formatAlertThreshold(value);
}

export function editorDefaults(placement: ChartAlertPlacement, latestPrice: number): TradingAlertEditorState {
  // A drawing whose tool defines a two-anchor alert level offers the line alert.
  const isTrendline = placement.trendlinePoints?.length === 2;
  const isIndicator = placement.indicatorId !== undefined || placement.chartIndicatorId !== undefined;
  return {
    mode: 'create',
    alertId: null,
    x: placement.x,
    y: placement.y,
    // A value read from an indicator pane keeps its precision (MACD on FX is well below 0.01).
    threshold: isTrendline ? '0' : isIndicator ? String(Number(placement.price.toPrecision(8))) : formatAlertThreshold(placement.price),
    condition: isTrendline
      ? 'trendline_crossing'
      : isIndicator
        ? 'indicator_above'
        : (placement.price >= latestPrice ? 'price_above' : 'price_below'),
    expiresAt: '',
    expiration: 'never',
    triggerPolicy: 'every_time',
    message: '',
    notifications: defaultAlertNotifications(),
    indicator: placement.indicatorId ?? 'rsi',
    period: String(placement.indicatorPeriod ?? 14),
    lookback: '1',
    trendlinePoints: isTrendline ? placement.trendlinePoints?.map((point) => ({ ...point })) : undefined,
    // A drawing's line alert follows the drawing (TVP-1.4): it starts on the first level the menu offers.
    ...(isTrendline && placement.drawingId && placement.drawingAlertLevels?.length
      ? { drawingId: placement.drawingId, drawingLevels: placement.drawingAlertLevels, drawingLevel: placement.drawingAlertLevels[0].key }
      : {}),
    ...(placement.chartIndicatorId ? { chartIndicatorId: placement.chartIndicatorId } : {}),
  };
}

export function TradingChartAlertOverlay({
  adapter,
  instrumentId,
  bindingId,
  interval,
  latestPrice,
  symbol,
  placement,
  onPlacementConsumed, indicatorChoices,
}: {
  adapter: TradingChartAdapter | null;
  instrumentId: string;
  bindingId: string | null;
  interval: string;
  latestPrice: number;
  symbol: string;
  placement: ChartAlertPlacement | null;
  onPlacementConsumed: () => void; /** The chart's indicators, for indicator alerts (TVP-1.3). */ indicatorChoices?: readonly AlertIndicatorChoice[];
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const alertsQuery = useTradingAlerts({ poll: true });
  const alertMutations = useTradingAlertMutations();
  const alerts = alertsQuery.data ?? [];
  const [status, setStatus] = useState<'loading' | 'ready' | 'saving' | 'conflict' | 'error'>('loading');
  const [editor, setEditor] = useState<TradingAlertEditorState | null>(null);
  const [dragging, setDragging] = useState<DragState | null>(null);
  const [, setCoordinateRevision] = useState(0);

  useEffect(() => {
    if (alertsQuery.isError) setStatus('error');
    else if (alertsQuery.isSuccess && status !== 'saving' && status !== 'conflict') setStatus('ready');
  }, [alertsQuery.isError, alertsQuery.isSuccess, status]);

  useEffect(() => {
    if (!adapter) return;
    let frame: number | null = null;
    let pointerActive = false;
    const invalidate = () => {
      if (frame !== null) return;
      frame = window.requestAnimationFrame(() => {
        frame = null;
        setCoordinateRevision((value) => value + 1);
      });
    };
    const visibleRange = adapter.onVisibleRange(invalidate);
    const crosshair = adapter.onCrosshair(invalidate);
    const viewport = adapter.onViewportChange(invalidate);
    const stage = rootRef.current?.parentElement;
    const insideStage = (target: EventTarget | null) => target instanceof Node && Boolean(stage?.contains(target));
    const pointerDown = (event: PointerEvent) => {
      if (!insideStage(event.target)) return;
      pointerActive = true;
      invalidate();
    };
    const pointerMove = (event: PointerEvent) => {
      if (pointerActive && insideStage(event.target)) invalidate();
    };
    const pointerUp = () => {
      if (!pointerActive) return;
      pointerActive = false;
      invalidate();
    };
    window.addEventListener('pointerdown', pointerDown);
    window.addEventListener('pointermove', pointerMove);
    window.addEventListener('pointerup', pointerUp);
    const resize = new ResizeObserver(() => setCoordinateRevision((value) => value + 1));
    if (rootRef.current) resize.observe(rootRef.current);
    return () => {
      visibleRange();
      crosshair();
      viewport();
      window.removeEventListener('pointerdown', pointerDown);
      window.removeEventListener('pointermove', pointerMove);
      window.removeEventListener('pointerup', pointerUp);
      if (frame !== null) window.cancelAnimationFrame(frame);
      resize.disconnect();
    };
  }, [adapter]);

  useEffect(() => {
    if (!placement) return;
    setEditor(editorDefaults(placement, latestPrice));
    onPlacementConsumed();
  }, [latestPrice, onPlacementConsumed, placement]);

  const visibleAlerts = useMemo(() => alerts.filter((alert) => (
    alert.instrument_id === instrumentId
    && (!alert.binding_id || !bindingId || alert.binding_id === bindingId)
    && (
      alert.condition_type.startsWith('price_')
      || alert.condition_type.startsWith('trendline_')
      || indicatorIdForAlert(alert) !== null
    )
  )), [alerts, bindingId, instrumentId]);
  const staticAlerts = visibleAlerts.filter((alert) => alert.condition_type.startsWith('price_'));
  const trendlineAlerts = visibleAlerts.filter((alert) => alert.condition_type.startsWith('trendline_'));
  const indicatorAlerts = visibleAlerts.filter((alert) => indicatorIdForAlert(alert) !== null);

  const runMutation = async (mutation: () => Promise<TradingAlert>, removedAlertId?: string) => {
    setStatus('saving');
    try {
      const updated = await mutation();
      if (removedAlertId) alertMutations.remove(removedAlertId);
      else alertMutations.replace(updated);
      notifyTradingAlertsChanged();
      await alertMutations.refresh();
      setEditor(null);
      setStatus('ready');
    } catch (error) {
      setStatus(error instanceof Error && error.message.includes('(409)') ? 'conflict' : 'error');
      await alertMutations.refresh();
    }
  };

  const saveEditor = async () => {
    if (!editor || editor.mode !== 'edit') return;
    const isTrendline = editor.condition.startsWith('trendline_');
    const threshold = isTrendline ? 0 : Number(editor.threshold);
    if (!Number.isFinite(threshold) || (isTrendline && editor.trendlinePoints?.length !== 2)) {
      setStatus('error');
      return;
    }
    const alert = alerts.find((item) => item.alert_id === editor.alertId);
    if (!alert) return;
    const input = chartAlertUpdateInput(alert, {
      threshold: isTrendline ? '0' : formatAlertThreshold(threshold),
      condition_type: editor.condition,
      indicator_id: editor.condition.startsWith('indicator_') ? editor.indicator : null,
      period: Number(editor.period) || 14,
      lookback_bars: Number(editor.lookback) || 1,
      expires_at: editor.expiresAt ? isoDateTime(editor.expiresAt) : expirationTimestamp(editor.expiration),
      trigger_policy: editor.triggerPolicy,
      message: editor.message, notification_channels: editor.notifications,
      ...deliveryUpdatePatch(editor),
    });
    if (isTrendline) {
      input.parameters = {
        ...input.parameters,
        trendline_points: editor.trendlinePoints?.map((point) => ({ ...point, price: String(point.price) })),
        trendline_mode: trendlineModeForCondition(editor.condition),
      };
    }
    if (applyConditionDrafts(input, editor, (problem) => setEditor((current) => current && { ...current, conditionError: problem }))) return void setStatus('error'); await runMutation(() => tradingApi.updateAlert(alert, input));
  };

  const createAlert = async () => {
    if (!editor || editor.mode !== 'create') return;
    const isTrendline = editor.condition.startsWith('trendline_');
    const threshold = isTrendline || isPriceLineAlert(editor.condition, editor.priceLine) ? 0 : Number(editor.threshold);
    if (!Number.isFinite(threshold) || (isTrendline && editor.trendlinePoints?.length !== 2)) {
      setStatus('error');
      return;
    }
    const input = chartAlertCreateInput({
      alertId: `chart-alert-${crypto.randomUUID()}`,
      instrumentId,
      bindingId,
      interval,
      threshold,
      latestPrice,
      condition: editor.condition === 'price_above' || editor.condition === 'price_below' ? editor.condition : undefined,
      expiration: editor.expiration,
      triggerPolicy: editor.triggerPolicy,
      message: editor.message,
      notificationChannels: editor.notifications, ...deliveryCreateFields(editor),
    });
    input.condition_type = editor.condition;
    input.threshold = isTrendline ? '0' : formatAlertThreshold(threshold);
    input.parameters = {
      ...input.parameters,
      indicator_id: editor.condition.startsWith('indicator_') ? editor.indicator : null,
      period: Number(editor.period) || 14,
      lookback_bars: Number(editor.lookback) || 1,
      trendline_points: isTrendline ? editor.trendlinePoints?.map((point) => ({ ...point, price: String(point.price) })) : null,
      trendline_mode: isTrendline ? trendlineModeForCondition(editor.condition) : null, ...(isTrendline && editor.drawingId ? { drawing_id: editor.drawingId, drawing_level: editor.drawingLevel ?? null } : {}),
    };
    input.expires_at = editor.expiresAt ? isoDateTime(editor.expiresAt) : input.expires_at; if (editor.condition.startsWith('indicator_') && indicatorChoices?.length && !withChartIndicatorCondition(input, indicatorChoices, resolveIndicatorSelection(indicatorChoices, editor.indicatorSelection, editor.chartIndicatorId), String(threshold))) return void setStatus('error'); withPriceLineCondition(input, editor.condition, editor.priceLine); if (applyConditionDrafts(input, editor, (problem) => setEditor((current) => current && { ...current, conditionError: problem }))) return void setStatus('error');
    await runMutation(() => tradingApi.createAlert(input));
  };

  const openEditor = (alert: TradingAlert, y: number) => {
    setEditor({
      mode: 'edit',
      alertId: alert.alert_id,
      x: Math.max(8, (rootRef.current?.clientWidth ?? 320) - 260),
      y,
      threshold: formatAlertThreshold(alert.threshold),
      condition: alert.condition_type,
      expiresAt: localDateTime(alert.expires_at),
      expiration: alert.expires_at ? '1d' : 'never',
      triggerPolicy: alertFrequency(alert),
      message: alert.parameters.message ?? '',
      notifications: alert.parameters.notification_channels ?? ['app', 'toast'],
      ...deliveryEditorOf(alert),
      indicator: alert.parameters.indicator_id ?? 'rsi',
      period: String(alert.parameters.period ?? 14),
      lookback: String(alert.parameters.lookback_bars ?? 1),
      trendlinePoints: alert.parameters.trendline_points?.map((point) => ({ time: point.time, price: Number(point.price) })), ...conditionEditorFields(alert),
    });
  };

  const beginDrag = (alert: TradingAlert) => (event: React.PointerEvent<SVGLineElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (!alert.enabled || alertVisualState(alert) === 'expired' || !adapter || !rootRef.current) return;
    const bounds = rootRef.current.getBoundingClientRect();
    const indicatorId = paneIndicatorId(alert);
    const valueFromCoordinate = (y: number) => indicatorId
      ? adapter.indicatorValueFromCoordinate(indicatorId, y)
      : adapter.priceFromCoordinate(y);
    setDragging({ alert, threshold: chartAlertThreshold(alert) });
    const move = (pointer: PointerEvent) => {
      const threshold = valueFromCoordinate(pointer.clientY - bounds.top);
      if (threshold !== null) setDragging({ alert, threshold });
    };
    const up = async (pointer: PointerEvent) => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      const threshold = valueFromCoordinate(pointer.clientY - bounds.top);
      setDragging(null);
      if (threshold === null || threshold === chartAlertThreshold(alert)) return;
      await runMutation(() => tradingApi.updateAlert(alert, thresholdUpdate(alert, threshold)));
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  const alertCoordinate = (alert: TradingAlert, threshold: number): number | null | undefined => {
    const indicatorId = paneIndicatorId(alert);
    return indicatorId
      ? adapter?.indicatorValueToCoordinateForId(indicatorId, threshold)
      : adapter?.priceToCoordinate(threshold);
  };

  const editorStyle = editor && rootRef.current ? {
    left: Math.max(8, Math.min(editor.x, rootRef.current.clientWidth - 450)),
    top: Math.max(8, Math.min(editor.y + 8, rootRef.current.clientHeight - 520)),
  } : undefined;

  return (
    <div ref={rootRef} className="trading-chart-alert-overlay" data-status={status}>
      <svg aria-label="Chart alert lines">
        {staticAlerts.map((alert) => {
          const threshold = dragging?.alert.alert_id === alert.alert_id ? dragging.threshold : chartAlertThreshold(alert);
          const y = adapter?.priceToCoordinate(threshold);
          if (y === null || y === undefined) return null;
          const state = alertVisualState(alert);
          return <line key={alert.alert_id} className={`trading-alert-line state-${state}`} x1="0" x2="100%" y1={y} y2={y} stroke={alertColor(state)} onPointerDown={beginDrag(alert)} />;
        })}
        {trendlineAlerts.map((alert) => {
          const points = alert.parameters.trendline_points;
          const first = points?.[0];
          const second = points?.[1];
          if (!first || !second || !adapter) return null;
          const firstCoordinate = adapter.projectDrawingPoint({ time: first.time, price: Number(first.price) });
          const secondCoordinate = adapter.projectDrawingPoint({ time: second.time, price: Number(second.price) });
          if (!firstCoordinate || !secondCoordinate) return null;
          const state = alertVisualState(alert);
          return (
            <line
              key={alert.alert_id}
              className={`trading-alert-line trading-alert-trendline state-${state}`}
              x1={firstCoordinate.x}
              y1={firstCoordinate.y}
              x2={secondCoordinate.x}
              y2={secondCoordinate.y}
              stroke={alertColor(state)}
              onPointerDown={(event) => event.stopPropagation()}
              onClick={() => openEditor(alert, secondCoordinate.y)}
            />
          );
        })}
        {indicatorAlerts.map((alert) => {
          const threshold = dragging?.alert.alert_id === alert.alert_id ? dragging.threshold : chartAlertThreshold(alert);
          const y = alertCoordinate(alert, threshold);
          if (y === null || y === undefined) return null;
          const state = alertVisualState(alert);
          return <line key={alert.alert_id} className={`trading-alert-line state-${state}`} x1="0" x2="100%" y1={y} y2={y} stroke={alertColor(state)} onPointerDown={beginDrag(alert)} />;
        })}
      </svg>

      {staticAlerts.map((alert) => {
        const threshold = dragging?.alert.alert_id === alert.alert_id ? dragging.threshold : chartAlertThreshold(alert);
        const y = adapter?.priceToCoordinate(threshold);
        if (y === null || y === undefined) return null;
        const state = alertVisualState(alert);
        const lastTriggered = alertLastTriggeredLabel(alert);
        return (
          <button
            key={`${alert.alert_id}:label`}
            type="button"
            className={`trading-alert-price-label state-${state}`}
            style={{ top: y }}
            title={`${state} alert · ${alert.condition_type.replace('price_', 'crosses ')} · requested feed ${alert.binding_id ?? 'default'}${lastTriggered ? ` · last triggered ${lastTriggered}` : ''} · revision ${alert.revision}`}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={() => openEditor(alert, y)}
          >
            <span aria-hidden="true">⏰</span>{formattedPrice(threshold)}<small>{state}</small>
          </button>
        );
      })}

      {indicatorAlerts.map((alert) => {
        const threshold = dragging?.alert.alert_id === alert.alert_id ? dragging.threshold : chartAlertThreshold(alert);
        const y = alertCoordinate(alert, threshold);
        if (y === null || y === undefined) return null;
        const state = alertVisualState(alert);
        const lastTriggered = alertLastTriggeredLabel(alert);
        return (
          <button
            key={`${alert.alert_id}:indicator-label`}
            type="button"
            className={`trading-alert-price-label state-${state}`}
            style={{ top: y }}
            title={`${state} indicator alert · ${alert.condition_type.replace('indicator_', 'crosses ')} · ${indicatorIdForAlert(alert) ?? 'indicator'} · revision ${alert.revision}${lastTriggered ? ` · last triggered ${lastTriggered}` : ''}`}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={() => openEditor(alert, y)}
          >
            <span aria-hidden="true">⏰</span>{formattedPrice(threshold)}<small>{state}</small>
          </button>
        );
      })}

      {editor ? (
        <div style={editorStyle} className="trading-chart-alert-editor-positioner">
          <TradingAlertDialog
            editor={editor}
            symbol={symbol} indicatorChoices={indicatorChoices}
            latestPrice={latestPrice}
            status={status}
            onChange={(patch) => setEditor((current) => current ? { ...current, ...patch } : current)}
            onSubmit={() => void (editor.mode === 'create' ? createAlert() : saveEditor())}
            onClose={() => setEditor(null)}
            onToggle={editor.mode === 'edit' ? () => {
              const alert = alerts.find((item) => item.alert_id === editor.alertId);
              if (alert) void runMutation(() => tradingApi.updateAlert(alert, chartAlertUpdateInput(alert, { enabled: !alert.enabled })));
            } : undefined}
            onArchive={editor.mode === 'edit' ? () => {
              const alert = alerts.find((item) => item.alert_id === editor.alertId);
              if (alert) void runMutation(() => tradingApi.archiveAlert(alert), alert.alert_id);
            } : undefined}
          />
        </div>
      ) : null}
    </div>
  );
}
