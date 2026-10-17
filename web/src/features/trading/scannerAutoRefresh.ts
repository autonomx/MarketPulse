// Screener auto-refresh (TVP-9.2): a saved screen re-runs every 10 s or every
// minute while the screener is open, and the results say what changed since
// the previous run. A run is started only when none of the screen's runs is
// still working, so a slow run never stacks up behind itself; the server's
// own limits (concurrency, runtime) bound each run.
import { useEffect, useRef } from 'react';
import { startPolling } from '../../shared/timers';
import type { TradingScannerResult } from './scannerTypes';

export const AUTO_REFRESH_OPTIONS = [
  { value: 0, label: 'Off' },
  { value: 10_000, label: 'Every 10 s' },
  { value: 60_000, label: 'Every minute' },
] as const;

export type AutoRefresh = { scannerId: string; everyMs: number };

/** A run still queued or running after this was abandoned (scanner_repository.STALE_RUN_SECONDS). */
export const STALE_RUN_MS = 600_000;

/** What changed between two runs' results: the instruments that are new, and those that dropped out. */
export function resultChanges(previous: readonly TradingScannerResult[] | null, next: readonly TradingScannerResult[]): { added: ReadonlySet<string>; removed: string[] } {
  if (previous === null) return { added: new Set(), removed: [] };
  const before = new Set(previous.map((result) => result.instrument_id));
  const after = new Set(next.map((result) => result.instrument_id));
  return {
    added: new Set([...after].filter((id) => !before.has(id))),
    removed: [...before].filter((id) => !after.has(id)),
  };
}

/** Failed starts in a row (a deleted or disabled screen, an outage) after which auto-refresh turns itself off. */
export const MAX_AUTO_REFRESH_FAILURES = 3;

/**
 * Starts the screen's runs on its cadence while mounted. A tick does nothing while a start is still in flight or
 * `busy` says one of its runs is working; the server also refuses a second run (409), which counts as busy.
 * After MAX_AUTO_REFRESH_FAILURES failed starts in a row, `onGiveUp` is told why.
 */
export function useScannerAutoRefresh(
  auto: AutoRefresh | null,
  busy: (scannerId: string) => boolean,
  start: (scannerId: string) => Promise<unknown>,
  onGiveUp: (error: unknown) => void = () => undefined,
): void {
  const latest = useRef({ busy, start, onGiveUp });
  useEffect(() => {
    latest.current = { busy, start, onGiveUp };
  });
  const scannerId = auto?.scannerId;
  const everyMs = auto?.everyMs ?? 0;
  useEffect(() => {
    if (!scannerId || everyMs <= 0) return;
    let inFlight = false;
    let failures = 0;
    return startPolling(async () => {
      if (inFlight || latest.current.busy(scannerId)) return;
      inFlight = true;
      try {
        await latest.current.start(scannerId);
        failures = 0;
      } catch (error) {
        if (error instanceof Error && error.message.includes('(409)')) return;
        failures += 1;
        if (failures >= MAX_AUTO_REFRESH_FAILURES) latest.current.onGiveUp(error);
      } finally {
        inFlight = false;
      }
    }, everyMs);
  }, [scannerId, everyMs]);
}
