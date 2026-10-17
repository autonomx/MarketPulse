import { useEffect, useState } from 'react';

/**
 * Timers for the web app (WP-9.6). Two kinds, kept apart:
 *
 * - `startPolling`: reading server state that has no server event. Runs on a
 *   setTimeout chain, so a slow request never overlaps the next one, and
 *   skips its turn while the page is hidden. Each caller uses an interval
 *   from `POLL_INTERVALS_MS`, which says why that resource is polled.
 * - `startTicker` / `useNow`: fixed-rate local cadence with no server
 *   involved: clocks and elapsed counters, replay playback, avatar mouth
 *   frames, capture sampling and local schedulers.
 *
 * Server state with events (job lifecycle on `/events`) is not polled; see
 * `events/useJobEventRefresh.ts`.
 */

/** Poll intervals for server state that has no event, and why. */
export const POLL_INTERVALS_MS = {
  /** Paper accounts, orders and positions: the paper broker publishes no events. */
  paperAccount: 5_000,
  /** The strategy command center's runtime and decisions: no runtime events. */
  strategyRuntime: 5_000,
  /** Scanner results refresh with the scanner run; it publishes no events. */
  scanner: 2_000,
  /** Strategy operations and prospective economics: slow-moving status without events. */
  strategyOperations: 30_000,
  /** Trading alerts and their triggers: alert evaluation publishes no events. */
  tradingAlerts: 10_000,
  /** Watchlist indicator columns: computed from bars on request; nothing publishes them. */
  watchlistIndicators: 60_000,
  /** Chart bars when the market stream is not delivering (closed or failed stream). */
  chartFallback: 30_000,
  /** A research job's progress (pages read, sources): job progress writes publish no events. */
  researchProgress: 1_500,
  /** An image model loading into memory: the image service has no event stream. */
  imageModelLoad: 750,
  /** The desktop companion's operational status (model and endpoint readiness). */
  desktopCompanionStatus: 30_000,
  /** A job whose lifecycle events cannot arrive because the event stream is down. */
  jobWithoutEvents: 1_000,
  /** A desktop Google sign-in finishing in the system browser: the gateway can't push to the window (MarketPulse S-5). */
  desktopGoogleSignIn: 1_500,
} as const;

/** Runs `task` every `intervalMs` after the previous run finishes, skipping turns while the page is hidden. */
export function startPolling(task: () => unknown, intervalMs: number): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  const schedule = () => {
    if (!stopped) timer = setTimeout(run, intervalMs);
  };
  const run = async () => {
    timer = null;
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
      schedule();
      return;
    }
    try {
      await task();
    } catch {
      // The task reports its own failures; polling continues.
    } finally {
      schedule();
    }
  };
  schedule();
  return () => {
    stopped = true;
    if (timer !== null) clearTimeout(timer);
  };
}

/** Calls `tick` every `intervalMs` at a fixed rate (local cadence only; see the module comment). */
export function startTicker(tick: () => void, intervalMs: number): () => void {
  const timer = setInterval(tick, intervalMs);
  return () => clearInterval(timer);
}

/** The current time, updated every `intervalMs` while `enabled`. */
export function useNow(intervalMs: number, enabled = true): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    if (!enabled) return undefined;
    setNow(new Date());
    return startTicker(() => setNow(new Date()), intervalMs);
  }, [enabled, intervalMs]);
  return now;
}
