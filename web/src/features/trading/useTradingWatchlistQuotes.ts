/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useEffect, useMemo, useState } from 'react';
import { tradingApi } from './tradingApi';
import type { ProviderBinding } from './tradingTypes';
import { isIntervalAvailable, tradingIntervalMinutes } from './tradingIntervals';
import { intervalBarStats, percentChangeFromBars, percentChangeFromLookback, type IntervalBarStats } from './tradingWatchlistChange';
import type { WatchlistSnapshot } from './tradingWatchlistColumns';

export type WatchlistQuoteSnapshot = WatchlistSnapshot;

/** Bars requested per symbol when the relative volume column is shown: the latest and 20 before it. */
const RELATIVE_VOLUME_BARS = 21;

/**
 * Symbols refreshed at once. Each symbol makes a quote and a bars request in
 * parallel (plus sequential fallbacks), so at most about twice this many
 * requests are in flight for a long list.
 */
export const WATCHLIST_QUOTE_CONCURRENCY = 4;

/** Run `task` for every item with at most `limit` running; stops starting new ones once `stopped()` is true. */
export async function runWithConcurrency<T>(
  items: readonly T[],
  limit: number,
  task: (item: T) => Promise<void>,
  stopped: () => boolean = () => false,
): Promise<void> {
  let next = 0;
  const worker = async () => {
    while (next < items.length && !stopped()) {
      const item = items[next];
      next += 1;
      await task(item);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
}

// The side-panel tabs unmount the watchlist when another tab is selected. Keep
// the latest values at module scope so returning to the watchlist can render
// them immediately while the next refresh is in flight.
const watchlistQuoteCache: Record<string, WatchlistQuoteSnapshot> = {};

const fallbackIntervals = ['1mo', '1w', '1d', '12h', '8h', '6h', '4h', '2h', '1h', '30m', '15m', '5m', '3m', '1m'];

function fallbackIntervalCandidates(interval: string): string[] {
  const targetMinutes = tradingIntervalMinutes(interval);
  if (targetMinutes == null) return [];
  return fallbackIntervals.filter((candidate) => {
    const candidateMinutes = tradingIntervalMinutes(candidate);
    return candidate !== interval && candidateMinutes != null && candidateMinutes < targetMinutes;
  });
}

function fallbackLimit(interval: string, baseInterval: string): number {
  const targetMinutes = tradingIntervalMinutes(interval) ?? 1;
  const baseMinutes = tradingIntervalMinutes(baseInterval) ?? targetMinutes;
  return Math.min(5_000, Math.max(2, Math.ceil(targetMinutes / baseMinutes) + 3));
}

function absoluteChange(price: string | null, changePercent: number | null): number | null {
  const current = Number(price);
  if (price == null || changePercent == null || !Number.isFinite(current) || changePercent <= -100) return null;
  // changePercent = (price - open) / open * 100, so price - open = price * changePercent / (100 + changePercent).
  return (current * changePercent) / (100 + changePercent);
}

function snapshotOf(price: string | null, changePercent: number | null, stats?: IntervalBarStats): WatchlistQuoteSnapshot {
  return { price, changePercent, change: absoluteChange(price, changePercent), ...stats };
}

async function intervalChange(
  instrumentId: string,
  interval: string,
  quotePrice: string | null | undefined,
  directBars: Awaited<ReturnType<typeof tradingApi.bars>> | null,
  supportedIntervals?: readonly string[],
): Promise<WatchlistQuoteSnapshot> {
  const directHistory = directBars?.bars ?? [];
  const directPrice = quotePrice ?? directHistory.at(-1)?.close?.toString() ?? null;
  if (supportedIntervals && !isIntervalAvailable(interval, supportedIntervals)) {
    return snapshotOf(directPrice, null);
  }
  const directIntervalIsNative = directBars?.binding.supported_intervals.includes(interval) ?? false;
  const directChange = directIntervalIsNative
    ? percentChangeFromBars(quotePrice, directHistory)
    : null;
  if (directChange != null) return snapshotOf(directPrice, directChange, intervalBarStats(directHistory));

  for (const baseInterval of fallbackIntervalCandidates(interval)) {
    const fallback = await tradingApi.bars(instrumentId, baseInterval, fallbackLimit(interval, baseInterval)).catch(() => null);
    const fallbackBars = fallback?.bars ?? [];
    const derivedChange = percentChangeFromLookback(quotePrice, fallbackBars, interval);
    if (derivedChange != null) {
      return snapshotOf(
        quotePrice ?? fallbackBars.at(-1)?.close?.toString() ?? directPrice,
        derivedChange,
        intervalBarStats(fallbackBars, interval),
      );
    }
  }

  return snapshotOf(directPrice, null);
}

/**
 * Latest price, interval change and interval bar statistics for each listed
 * symbol. `withRelativeVolume` requests enough bars to compare the latest
 * volume with the bars before it; the latest bar is usually still forming.
 * Fetches follow the set of symbols, so reordering or sorting the list does
 * not refetch.
 */
export function useTradingWatchlistQuotes(
  instrumentIds: readonly string[],
  interval: string,
  providerBindings: readonly ProviderBinding[],
  withRelativeVolume = false,
): Record<string, WatchlistQuoteSnapshot> {
  const [quotes, setQuotes] = useState<Record<string, WatchlistQuoteSnapshot>>(() => ({ ...watchlistQuoteCache }));
  const instrumentIdsKey = [...new Set(instrumentIds)].sort().join('\u0000');
  const bindingIntervalsByInstrument = useMemo(() => {
    const bindingsByInstrument = new Map<string, readonly string[]>();
    for (const binding of providerBindings) {
      if (!binding.supported_intervals.length || bindingsByInstrument.has(binding.instrument_id)) continue;
      bindingsByInstrument.set(binding.instrument_id, binding.supported_intervals);
    }
    return bindingsByInstrument;
  }, [providerBindings]);

  useEffect(() => {
    const ids = instrumentIdsKey ? instrumentIdsKey.split('\u0000') : [];
    let cancelled = false;
    if (ids.length === 0) {
      return () => { cancelled = true; };
    }

    void (async () => {
      const next: Record<string, WatchlistQuoteSnapshot> = {};
      await runWithConcurrency(ids, WATCHLIST_QUOTE_CONCURRENCY, async (instrumentId) => {
        const supportedIntervals = bindingIntervalsByInstrument.get(instrumentId);
        try {
          const quotePromise = tradingApi.quote(instrumentId).catch(() => null);
          const barsPromise = supportedIntervals && !isIntervalAvailable(interval, supportedIntervals)
            ? Promise.resolve(null)
            : tradingApi.bars(instrumentId, interval, withRelativeVolume ? RELATIVE_VOLUME_BARS : 2).catch(() => null);
          const [quote, bars] = await Promise.all([
            quotePromise,
            barsPromise,
          ]);
          next[instrumentId] = await intervalChange(instrumentId, interval, quote?.price, bars, supportedIntervals);
        } catch {
          next[instrumentId] = { price: null, changePercent: null };
        }
      }, () => cancelled);
      if (!cancelled) {
        setQuotes((current) => {
          const merged = { ...current };
          for (const [instrumentId, snapshot] of Object.entries(next)) {
            // Keep the last known snapshot when a refresh fails. A symbol that
            // has never produced a quote still renders the normal placeholder.
            if (snapshot.price === null && snapshot.changePercent === null) {
              const cached = watchlistQuoteCache[instrumentId] ?? current[instrumentId];
              if (cached) {
                merged[instrumentId] = cached;
                continue;
              }
            }
            watchlistQuoteCache[instrumentId] = snapshot;
            merged[instrumentId] = snapshot;
          }
          return merged;
        });
      }
    })();

    return () => { cancelled = true; };
  }, [instrumentIdsKey, interval, withRelativeVolume]);

  return quotes;
}
