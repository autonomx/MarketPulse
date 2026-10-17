import { useEffect, useMemo, useRef, useState } from 'react';
import type { TradingChartAdapter } from './chart/chartAdapter';
import { chartAccessFor } from './drawings/drawingFrame';
import type { TradingDrawing } from './drawings/drawingCommands';
import { drawingAlertLevelKeys, drawingMenuEntries } from './drawings/TradingDrawingOverlay';
import type { DrawingAlertLevel, DrawingInstrument } from './drawings/tools/types';
import { tradingApi } from './tradingApi';
import { chartAlertUpdateInput, notifyTradingAlertsChanged } from './tradingChartAlerts';
import type { TradingAlert } from './tradingTypes';
import { useTradingAlertMutations, useTradingAlerts } from './useTradingAlerts';

/** How long a drawing must stay still before its alerts follow it (a drag sends many moves). */
export const DRAWING_ALERT_SETTLE_MS = 600;
/** Failed updates of one alert after which the sync leaves it until the page reloads. */
const MAX_SYNC_FAILURES = 2;

type Point = { time: string; price: number };

/** The line's price at `time`, as the server evaluates a trendline: straight in time, extended both ways. */
function lineAt(points: readonly Point[], time: string): number | null {
  const [first, second] = points;
  if (!first || !second) return null;
  const t0 = Date.parse(first.time);
  const t1 = Date.parse(second.time);
  if (!Number.isFinite(t0) || !Number.isFinite(t1) || t0 === t1) return null;
  return first.price + (second.price - first.price) * ((Date.parse(time) - t0) / (t1 - t0));
}

/**
 * Whether two lines are the same line for the server. Anchors can differ without the line changing: a flat level's
 * second anchor is one bar after the first, so charts on different intervals place it differently.
 */
export function sameAlertLine(current: readonly Point[], next: readonly Point[]): boolean {
  if (current.length !== 2 || next.length !== 2) return false;
  return next.every((point) => {
    const value = lineAt(current, point.time);
    return value !== null && Math.abs(value - point.price) <= Math.max(Math.abs(point.price) * 1e-9, 1e-12);
  });
}

/** The alerts on this chart's instrument that follow a drawing (TVP-1.4). */
export function drawingLinkedAlerts(alerts: readonly TradingAlert[], instrumentId: string): TradingAlert[] {
  return alerts.filter((alert) => alert.instrument_id === instrumentId && alert.parameters.drawing_id && alert.condition_type.startsWith('trendline_'));
}

/**
 * What the linked alerts need now: the line of each whose drawing level moved, and the alerts whose drawing is here
 * but no longer has their level (a level that went away is treated like a deleted drawing).
 */
export function drawingAlertUpdates(
  alerts: readonly TradingAlert[],
  drawings: readonly TradingDrawing[],
  levelsOf: (drawing: TradingDrawing) => readonly DrawingAlertLevel[],
  /** The keys the drawing has at all; a level it has but this chart can't offer is skipped, not lost. */
  keysOf: (drawing: TradingDrawing) => ReadonlySet<string> = (drawing) => new Set(levelsOf(drawing).map((level) => level.key)),
): { moved: Array<{ alert: TradingAlert; points: Point[] }>; lost: TradingAlert[] } {
  const moved: Array<{ alert: TradingAlert; points: Point[] }> = [];
  const lost: TradingAlert[] = [];
  for (const alert of alerts) {
    const drawing = drawings.find((item) => item.drawingId === alert.parameters.drawing_id);
    if (!drawing) continue;
    const levels = levelsOf(drawing);
    const level = levels.find((item) => item.key === alert.parameters.drawing_level) ?? (alert.parameters.drawing_level ? undefined : levels[0]);
    if (!level) {
      if (alert.parameters.drawing_level && !keysOf(drawing).has(alert.parameters.drawing_level)) lost.push(alert);
      continue;
    }
    const points = level.anchors.map((point) => ({ time: point.time, price: point.price }));
    const current = (alert.parameters.trendline_points ?? []).map((point) => ({ time: point.time, price: Number(point.price) }));
    if (!sameAlertLine(current, points)) moved.push({ alert, points });
  }
  return { moved, lost };
}

/**
 * Keeps a drawing's line alerts on the drawing (TVP-1.4). Only the active chart writes, and never in replay (its
 * bars are a replay's): once a drawing settles after a move, its alerts take the level's new line. A drawing deleted
 * on this chart, or a level that went away, offers its alerts for disabling.
 */
// Alerts the user kept on a prompt: not offered again on this page, on any chart.
const keptAlerts = new Set<string>();

/** Bars enough to place a level (a step between bars): before them every level reads as missing. */
const MIN_SYNC_BARS = 2;

export function DrawingAlertSync({
  adapter, instrumentId, instrument, drawings, active, replayMode,
}: {
  adapter: TradingChartAdapter | null;
  instrumentId: string;
  instrument: DrawingInstrument;
  drawings: readonly TradingDrawing[];
  active: boolean;
  replayMode: boolean;
}) {
  const alerts = useTradingAlerts().data;
  const mutationsHook = useTradingAlertMutations();
  // A stable handle: the settle timer must not restart because the hook returned a new object.
  const mutationsRef = useRef(mutationsHook);
  useEffect(() => {
    mutationsRef.current = mutationsHook;
  });
  const linked = useMemo(() => drawingLinkedAlerts(alerts ?? [], instrumentId), [alerts, instrumentId]);
  // Alerts whose drawing was deleted here, or whose level the drawing no longer has.
  const [orphans, setOrphans] = useState<Array<{ alert: TradingAlert; reason: 'deleted' | 'level' }>>([]);
  const [error, setError] = useState<string | null>(null);
  const seen = useRef<Set<string>>(new Set());
  const failures = useRef(new Map<string, number>());
  const attempted = useRef(new Map<string, string>());
  const { tickSize, pointValue } = instrument;

  useEffect(() => {
    if (!adapter || !active || replayMode || linked.length === 0) return;
    let timer: ReturnType<typeof window.setTimeout> | undefined;
    const settle = () => {
      // No bars yet (first load, an interval switch): no level can be placed, so none is lost; look again later.
      if (adapter.drawingBars().length < MIN_SYNC_BARS) {
        timer = window.setTimeout(settle, DRAWING_ALERT_SETTLE_MS);
        return;
      }
      const access = chartAccessFor(adapter, { tickSize, pointValue });
      const matches = adapter.drawingBarIndexMatchesBars();
      const { moved, lost } = drawingAlertUpdates(
        linked, drawings, (drawing) => drawingMenuEntries(drawing, access, matches).drawingAlertLevels ?? [], (drawing) => drawingAlertLevelKeys(drawing, access),
      );
      // The level orphans are what this pass found (a level that came back takes its alert off the prompt), less the
      // ones the user chose to keep.
      const lostEnabled = lost.filter((alert) => alert.enabled && !keptAlerts.has(alert.alert_id));
      setOrphans((current) => {
        const deleted = current.filter((item) => item.reason === 'deleted');
        const next = [...deleted, ...lostEnabled.filter((alert) => !deleted.some((item) => item.alert.alert_id === alert.alert_id)).map((alert) => ({ alert, reason: 'level' as const }))];
        return next.length === current.length && next.every((item, index) => item.alert === current[index].alert) ? current : next;
      });
      for (const { alert, points } of moved) {
        // A failing alert is retried when its drawing moves again (new points), not on every pass.
        const target = JSON.stringify(points);
        if (attempted.current.get(alert.alert_id) !== target) failures.current.delete(alert.alert_id);
        attempted.current.set(alert.alert_id, target);
        if ((failures.current.get(alert.alert_id) ?? 0) >= MAX_SYNC_FAILURES) continue;
        const input = chartAlertUpdateInput(alert, {});
        input.parameters = { ...input.parameters, trendline_points: points.map((point) => ({ ...point, price: String(point.price) })) };
        void tradingApi.updateAlert(alert, input).then((updated) => {
          failures.current.delete(alert.alert_id);
          mutationsRef.current.replace(updated);
          notifyTradingAlertsChanged();
        }).catch(() => {
          // A conflict (another edit got there first) retries against the refreshed alert; a lasting failure stops.
          failures.current.set(alert.alert_id, (failures.current.get(alert.alert_id) ?? 0) + 1);
          void mutationsRef.current.refresh();
        });
      }
    };
    timer = window.setTimeout(settle, DRAWING_ALERT_SETTLE_MS);
    return () => window.clearTimeout(timer);
  }, [active, adapter, drawings, linked, pointValue, replayMode, tickSize]);

  // Only a deletion seen here counts: a drawing that was on this chart and is gone (another chart's drawing never is).
  // A drawing that comes back (undo) takes its alerts off the prompt.
  useEffect(() => {
    const now = new Set(drawings.map((drawing) => drawing.drawingId));
    const gone = linked.filter((alert) => alert.enabled && seen.current.has(alert.parameters.drawing_id as string) && !now.has(alert.parameters.drawing_id as string));
    setOrphans((current) => {
      const kept = current.filter((item) => item.reason === 'level' || !now.has(item.alert.parameters.drawing_id as string));
      const next = [...kept, ...gone.filter((alert) => !kept.some((item) => item.alert.alert_id === alert.alert_id)).map((alert) => ({ alert, reason: 'deleted' as const }))];
      return next.length === current.length && next.every((item, index) => item.alert === current[index].alert) ? current : next;
    });
    seen.current = now;
  }, [drawings, linked]);

  // One chart asks: the active one (charts sharing drawings would all ask otherwise), and only about alerts still armed
  // and not kept (here or on another chart).
  const asking = orphans.filter((item) => !keptAlerts.has(item.alert.alert_id) && (alerts ?? []).some((alert) => alert.alert_id === item.alert.alert_id && alert.enabled));
  if (asking.length === 0 || !active) return null;
  const disable = () => {
    setError(null);
    // The alerts as they are now: one disabled meanwhile (here or elsewhere) needs nothing.
    const pending = asking.map((item) => (alerts ?? []).find((alert) => alert.alert_id === item.alert.alert_id) ?? item.alert).filter((alert) => alert.enabled);
    void Promise.allSettled(pending.map((alert) => tradingApi.updateAlert(alert, chartAlertUpdateInput(alert, { enabled: false })))).then((outcomes) => {
      const failed = new Set<string>();
      outcomes.forEach((outcome, index) => {
        if (outcome.status === 'fulfilled') mutationsRef.current.replace(outcome.value);
        else failed.add(pending[index].alert_id);
      });
      notifyTradingAlertsChanged();
      setOrphans((current) => current.filter((item) => failed.has(item.alert.alert_id)));
      if (failed.size > 0) {
        setError('Some could not be disabled; they are still armed. Try again.');
        void mutationsRef.current.refresh();
      }
    });
  };
  const keep = () => {
    asking.forEach((item) => keptAlerts.add(item.alert.alert_id));
    setOrphans([]);
    setError(null);
  };
  return (
    <div className="trading-drawing-alert-prompt" role="alertdialog" aria-label="Alerts of a deleted drawing" onPointerDown={(event) => event.stopPropagation()}>
      <span>{asking.length === 1 ? 'An alert follows' : `${asking.length} alerts follow`} a drawing or level that is gone. Disable {asking.length === 1 ? 'it' : 'them'}?</span>
      {error ? <small role="alert">{error}</small> : null}
      <button type="button" onClick={disable}>Disable</button>
      <button type="button" onClick={keep}>Keep</button>
    </div>
  );
}
