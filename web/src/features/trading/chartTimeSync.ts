/**
 * Time sync between a tab's charts (TVP-4.2): with the layout's Time link on, clicking a bar on one chart scrolls the
 * others to that time, loading older history when they need it, whatever their interval.
 */
import { useEffect, useRef } from 'react';
import { emitOmnixEvent, onOmnixEvent } from '../../events/bus';
import { useTradingStore } from './tradingStore';

declare module '../../events/bus' {
  interface OmnixEventMap {
    'omnix:trading-time-sync': { sourceChartId: string; timeMs: number };
  }
}

type BarClickSource = { onBarClick: (listener: (timeMs: number) => void) => () => void };

/** One chart's side: publishes its bar clicks and follows the other charts' while the link is on. */
export function useChartTimeSync(chartId: string, adapter: BarClickSource | null, goToTime: (timeMs: number) => unknown): void {
  const enabled = useTradingStore((state) => Boolean(state.links.time));
  const follow = useRef(goToTime);
  useEffect(() => {
    follow.current = goToTime;
  });
  useEffect(() => {
    if (!enabled || !adapter) return undefined;
    return adapter.onBarClick((timeMs) => emitOmnixEvent('omnix:trading-time-sync', { sourceChartId: chartId, timeMs }));
  }, [adapter, chartId, enabled]);
  useEffect(() => (enabled
    ? onOmnixEvent('omnix:trading-time-sync', ({ sourceChartId, timeMs }) => {
      if (sourceChartId !== chartId) follow.current(timeMs);
    })
    : undefined), [chartId, enabled]);
}
