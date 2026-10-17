import { useEffect, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { tradingApi } from './tradingApi';
import type { TradingAlert } from './tradingTypes';
import { POLL_INTERVALS_MS, startPolling } from '../../shared/timers';

export const TRADING_ALERTS_QUERY_KEY = ['trading', 'alerts'] as const;
export const TRADING_ALERT_TRIGGERS_QUERY_KEY = ['trading', 'alert-triggers'] as const;

let pollSubscribers = 0;
let stopAlertPolling: (() => void) | null = null;
let triggerPollSubscribers = 0;
let stopTriggerPolling: (() => void) | null = null;

export function useTradingAlerts(options: { poll?: boolean } = {}) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: TRADING_ALERTS_QUERY_KEY,
    queryFn: tradingApi.alerts,
    staleTime: 5_000,
  });

  useEffect(() => {
    if (!options.poll) return;
    pollSubscribers += 1;
    if (!stopAlertPolling) {
      stopAlertPolling = startPolling(() => queryClient.invalidateQueries({ queryKey: TRADING_ALERTS_QUERY_KEY }), POLL_INTERVALS_MS.tradingAlerts);
    }
    return () => {
      pollSubscribers = Math.max(0, pollSubscribers - 1);
      if (pollSubscribers === 0 && stopAlertPolling) {
        stopAlertPolling();
        stopAlertPolling = null;
      }
    };
  }, [options.poll, queryClient]);

  return query;
}

export function useTradingAlertMutations() {
  const queryClient = useQueryClient();

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: TRADING_ALERTS_QUERY_KEY });
  };

  const replace = (alert: TradingAlert) => {
    queryClient.setQueryData<TradingAlert[]>(TRADING_ALERTS_QUERY_KEY, (current = []) => {
      const exists = current.some((item) => item.alert_id === alert.alert_id);
      return exists
        ? current.map((item) => item.alert_id === alert.alert_id ? alert : item)
        : [alert, ...current];
    });
  };

  const remove = (alertId: string) => {
    queryClient.setQueryData<TradingAlert[]>(TRADING_ALERTS_QUERY_KEY, (current = []) => (
      current.filter((alert) => alert.alert_id !== alertId)
    ));
  };

  return { refresh, replace, remove };
}

export function useTradingAlertTriggers(options: { poll?: boolean } = {}) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: TRADING_ALERT_TRIGGERS_QUERY_KEY,
    queryFn: tradingApi.alertTriggers,
    staleTime: 5_000,
  });

  useEffect(() => {
    if (!options.poll) return;
    triggerPollSubscribers += 1;
    if (!stopTriggerPolling) {
      stopTriggerPolling = startPolling(() => queryClient.invalidateQueries({ queryKey: TRADING_ALERT_TRIGGERS_QUERY_KEY }), POLL_INTERVALS_MS.tradingAlerts);
    }
    return () => {
      triggerPollSubscribers = Math.max(0, triggerPollSubscribers - 1);
      if (triggerPollSubscribers === 0 && stopTriggerPolling) {
        stopTriggerPolling();
        stopTriggerPolling = null;
      }
    };
  }, [options.poll, queryClient]);

  return query;
}

/** The indicators the server evaluates for alerts (TVP-1.3); null while loading or unavailable. */
export function useAlertIndicatorIds(): ReadonlySet<string> | null {
  const query = useQuery({ queryKey: ['trading', 'alerts', 'indicators'], queryFn: tradingApi.alertIndicators, staleTime: 60 * 60_000 });
  return useMemo(() => (query.data ? new Set(query.data) : null), [query.data]);
}
