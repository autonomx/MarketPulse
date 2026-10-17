/**
 * Earnings (E), dividend (D) and split (S) markers along the bottom of a stock chart's price pane (TVP-10.1), with
 * each event's details on hover or focus. Upcoming events sit past the last bar, except in replay.
 */
import { useEffect, useId, useMemo, useState } from 'react';
import type { TradingChartAdapter } from './chart/chartAdapter';
import { intervalStepMs } from './drawings/tools/barTimeline';
import { describeEvent, EVENT_KINDS, EVENT_LABEL, EVENT_LETTER, eventAnchors, useCompanyEvents, type EventAnchor } from './corporateEvents';
import { isIntradayInterval } from './tradingIntervals';
import { useTradingReplayStore } from './tradingReplayStore';
import './TradingChartEventMarkers.css';

type Placed = EventAnchor & { x: number };
const MARKER_SIZE = 16;

type Props = {
  adapter: TradingChartAdapter;
  instrumentId: string;
  interval: string;
  replayMode: boolean;
  /** Changes whenever the chart's bars do. */
  barsRevision: unknown;
};

export function TradingChartEventMarkers({ adapter, instrumentId, interval, replayMode, barsRevision }: Props) {
  const query = useCompanyEvents(instrumentId);
  const replayClock = useTradingReplayStore((state) => (replayMode ? state.clock : null));
  const [placed, setPlaced] = useState<{ markers: Placed[]; bottom: number }>({ markers: [], bottom: 0 });
  const [open, setOpen] = useState<string | null>(null);
  const cardId = useId();
  const events = query.data?.events;
  const options = useMemo(() => ({ intraday: isIntradayInterval(interval), stepMs: intervalStepMs(interval), upcoming: !replayMode }), [interval, replayMode]);

  useEffect(() => {
    if (!events?.length) {
      setPlaced({ markers: [], bottom: 0 });
      return undefined;
    }
    const update = () => {
      const anchors = eventAnchors(events, adapter.drawingBars(), options);
      const width = adapter.indicatorPlotWidth();
      const markers = anchors.flatMap((anchor) => {
        const x = anchor.index === null ? adapter.timeToCoordinate(anchor.time) : adapter.barTimeToCoordinate(anchor.time) ?? adapter.timeToCoordinate(anchor.time);
        return x === null || x < 0 || x > width ? [] : [{ ...anchor, x }];
      });
      setPlaced({ markers, bottom: adapter.mainPaneHeight() ?? 0 });
    };
    const frame = window.requestAnimationFrame(update);
    const unsubscribe = adapter.onViewportChange(update);
    return () => {
      window.cancelAnimationFrame(frame);
      unsubscribe();
    };
  }, [adapter, events, options, barsRevision, replayClock]);

  if (placed.markers.length === 0 || placed.bottom <= MARKER_SIZE) return null;
  const shown = placed.markers.find((marker) => marker.key === open);
  return (
    <div className="trading-chart-events" aria-label="Earnings, dividends and splits">
      {placed.markers.map((marker) => {
        const row = EVENT_KINDS.indexOf(marker.kind);
        const label = marker.events.map(describeEvent).join('; ');
        return (
          <button
            key={marker.key}
            type="button"
            className={`trading-chart-event is-${marker.kind}${marker.events.every((event) => event.estimated) ? ' is-estimated' : ''}`}
            style={{ left: marker.x - MARKER_SIZE / 2, top: placed.bottom - (row + 1) * (MARKER_SIZE + 2) - 2 }}
            aria-label={label}
            aria-describedby={open === marker.key ? cardId : undefined}
            onPointerEnter={() => setOpen(marker.key)}
            onPointerLeave={() => setOpen((current) => (current === marker.key ? null : current))}
            onFocus={() => setOpen(marker.key)}
            onBlur={() => setOpen((current) => (current === marker.key ? null : current))}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={() => {
              const link = marker.events.find((event) => event.link)?.link;
              if (link) window.open(link, '_blank', 'noopener,noreferrer');
            }}
          >
            {EVENT_LETTER[marker.kind]}
          </button>
        );
      })}
      {shown ? (
        <div
          id={cardId}
          role="tooltip"
          className="trading-chart-event-card"
          style={{ left: shown.x, top: placed.bottom - (EVENT_KINDS.indexOf(shown.kind) + 1) * (MARKER_SIZE + 2) - 6 }}
        >
          <strong>{EVENT_LABEL[shown.kind]}</strong>
          {shown.events.map((event) => <span key={`${event.kind}-${event.date}`}>{describeEvent(event)}</span>)}
          {shown.events.some((event) => event.estimated) ? <small>Estimated from the same quarter a year earlier; not announced.</small> : null}
          {shown.events.some((event) => event.link) ? <small>Click to open the SEC filing.</small> : null}
        </div>
      ) : null}
    </div>
  );
}
