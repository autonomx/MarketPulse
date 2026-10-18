import { useQuery } from '@tanstack/react-query';
import { POLL_INTERVALS_MS } from '../../shared/timers';
import { tradingApi } from './tradingApi';
import { isIntervalAvailable, isIntradayInterval, tradingIntervalDurationMs } from './tradingIntervals';
import type { ProviderBinding, ProviderDescriptor } from './tradingTypes';

/**
 * Equity charts with no chosen feed use IBKR up to this interval while IB Gateway is
 * connected; longer intervals keep Yahoo, whose history goes back much further.
 */
export const IBKR_AUTO_MAX_INTERVAL_MS = 30 * 60_000;

/** Whether a chart on this binding gets live bars over the Trading stream; otherwise it polls. */
export function bindingStreamsInterval(
  binding: Pick<ProviderBinding, 'provider' | 'feed_type' | 'supported_intervals'>,
  interval: string,
): boolean {
  if (binding.feed_type === 'websocket_and_rest') return binding.supported_intervals.includes(interval);
  // IBKR streams 1m bars and the server rebuilds longer intraday intervals from them
  // (streaming/ibkr_stream.py), so any intraday interval its 1m bars can serve streams.
  if (binding.provider === 'ibkr') return isIntradayInterval(interval) && isIntervalAvailable(interval, binding.supported_intervals);
  return false;
}

/** The IBKR binding an equity chart with no chosen feed should use now, if any. */
export function autoChartBindingId(
  instrumentId: string,
  interval: string,
  providers: readonly ProviderDescriptor[] | undefined,
): string | null {
  if (!instrumentId.startsWith('equity:')) return null;
  const ibkr = providers?.find((provider) => provider.provider === 'ibkr');
  if (!ibkr?.enabled || ibkr.status !== 'ready') return null;
  const durationMs = tradingIntervalDurationMs(interval);
  if (durationMs === null || durationMs > IBKR_AUTO_MAX_INTERVAL_MS) return null;
  const binding = ibkr.bindings.find((item) => item.instrument_id === instrumentId);
  return binding && bindingStreamsInterval(binding, interval) ? binding.binding_id : null;
}

/** The feed a chart reads: the one chosen for it, else IBKR when it applies, else the instrument's default (null). */
export function chartBindingId(
  chart: { instrumentId: string; interval: string; bindingId: string | null },
  providers: readonly ProviderDescriptor[] | undefined,
): string | null {
  return chart.bindingId ?? autoChartBindingId(chart.instrumentId, chart.interval, providers);
}

/** Market-data providers and their status, refreshed so charts notice IB Gateway starting or stopping. */
export function useTradingProviders() {
  return useQuery({
    queryKey: ['trading', 'providers'],
    queryFn: tradingApi.providers,
    refetchInterval: POLL_INTERVALS_MS.marketDataProviders,
  });
}
