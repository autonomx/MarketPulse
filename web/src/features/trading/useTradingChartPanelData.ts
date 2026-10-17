import { useTradingReplayStore } from './tradingReplayStore';
import { useQueries, useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo } from 'react';
import { tradingApi } from './tradingApi';
import { TradingChartAdapter, normalizeChartBars, type TradingComparisonData } from './chart/chartAdapter';
import { indicatorUsesSeparatePane } from './indicators/coreIndicators';
import type { MarketBar } from './tradingTypes';
import { useChartReplayClock } from './useTradingChartPanelReplayClock';
import { TRADING_COMPARISON_COLORS } from './tradingComparisons';
import { resolveTradingTimezone } from './tradingTime';
import { TradingChartPanelProps, chartHistoryLimit, comparisonBars, comparisonBarsQueryKey, comparisonLabel } from './tradingChartPanelModel';
import { filterExtendedHours } from './tradingChartWorkflow';
import type { useChartPanelState } from './useTradingChartPanelState';

/** Indicator scheduling: recomputes indicator outputs off the render path. */
export function useChartIndicatorScheduling(ws: TradingChartPanelProps & ReturnType<typeof useChartPanelState>) {
  const {
    adapterRef, barsRef, fullscreenIndicatorRef, fullscreenMainPaneRef, historyLimitOverride, indicatorBindingRef, indicatorSchedulerRef, indicatorSessionRef,
    indicatorTimerRef, indicators, indicatorsRef, instrumentId, interval, minimizedIndicatorsRef, onActivate, replayMode,
    onActivateRef, setAlertPlacement, setIndicatorError, setIndicatorOutputs, setIndicatorPaneGeometry,
  } = ws;

  useEffect(() => {
    onActivateRef.current = onActivate;
  }, [onActivate, onActivateRef]);

  const clearAlertPlacement = useCallback(() => setAlertPlacement(null), [setAlertPlacement]);

  const refreshIndicatorPanes = useCallback((targetAdapter?: TradingChartAdapter | null) => {
    if (!targetAdapter) {
      setIndicatorPaneGeometry([]);
      return;
    }
    try {
      setIndicatorPaneGeometry(targetAdapter.indicatorPaneGeometry());
    } catch {
      setIndicatorPaneGeometry([]);
    }
  }, [setIndicatorPaneGeometry]);

  const scheduleIndicators = useCallback((delay = 0) => {
    if (indicatorTimerRef.current) clearTimeout(indicatorTimerRef.current);
    indicatorTimerRef.current = setTimeout(() => {
      indicatorTimerRef.current = null;
      const scheduler = indicatorSchedulerRef.current;
      const targetAdapter = adapterRef.current;
      if (!scheduler || !targetAdapter) return;
      // Replaying, intrabar indicators read no lower bar after the clock.
      const clock = replayMode ? useTradingReplayStore.getState().clock : null;
      void scheduler.calculate(barsRef.current, indicatorsRef.current, { session: indicatorSessionRef.current, bindingId: indicatorBindingRef.current, clock })
        .then((outputs) => {
          if (outputs && adapterRef.current === targetAdapter) {
            targetAdapter.setIndicatorOutputs(outputs);
            setIndicatorOutputs(outputs);
            if (fullscreenIndicatorRef.current) {
              targetAdapter.setIndicatorPaneFullscreen(fullscreenIndicatorRef.current);
            } else if (fullscreenMainPaneRef.current) {
              targetAdapter.setMainPaneFullscreen(true);
            } else {
              for (const indicator of indicatorsRef.current) {
                if (indicatorUsesSeparatePane(indicator.id)) {
                  targetAdapter.setIndicatorPaneMinimized(indicator.id, minimizedIndicatorsRef.current.has(indicator.id));
                }
              }
            }
            refreshIndicatorPanes(targetAdapter);
            window.requestAnimationFrame(() => {
              if (adapterRef.current !== targetAdapter) return;
              if (fullscreenIndicatorRef.current) {
                targetAdapter.setIndicatorPaneFullscreen(fullscreenIndicatorRef.current);
              } else if (fullscreenMainPaneRef.current) {
                targetAdapter.setMainPaneFullscreen(true);
              } else {
                for (const indicator of indicatorsRef.current) {
                  if (indicatorUsesSeparatePane(indicator.id)) {
                    targetAdapter.setIndicatorPaneMinimized(indicator.id, minimizedIndicatorsRef.current.has(indicator.id));
                  }
                }
              }
              refreshIndicatorPanes(targetAdapter);
              window.requestAnimationFrame(() => refreshIndicatorPanes(targetAdapter));
            });
            setIndicatorError(null);
          }
        })
        .catch((error) => setIndicatorError(error instanceof Error ? error.message : String(error)));
    }, delay);
  }, [refreshIndicatorPanes, adapterRef, barsRef, indicatorBindingRef, replayMode, fullscreenIndicatorRef, fullscreenMainPaneRef, indicatorSchedulerRef, indicatorSessionRef, indicatorTimerRef, indicatorsRef, minimizedIndicatorsRef, setIndicatorError, setIndicatorOutputs]);

  const defaultHistoryLimit = chartHistoryLimit(instrumentId, interval, indicators);

  // Go to date (TVP-2.5) asks for more history on the same bars request; it applies to this symbol and interval only.
  const historyLimit = historyLimitOverride?.key === `${instrumentId}|${interval}`
    ? Math.max(defaultHistoryLimit, historyLimitOverride.limit)
    : defaultHistoryLimit;

  return {
    clearAlertPlacement, refreshIndicatorPanes, scheduleIndicators, historyLimit,
  };
}

/** Bars, comparisons and currency rates for the chart, and the replay window. */
export function useChartPanelData(ws: TradingChartPanelProps & ReturnType<typeof useChartPanelState> & ReturnType<typeof useChartIndicatorScheduling>) {
  const {
    active, adapter, bindingId, chartSettings, comparisons, historyLimit, indicators, instrumentId, interval,
    onActivateRef, priceScaleCurrency, replayMode, rightOffset,
    selectedIndicator, setPriceScaleCurrency, setSelectedIndicator, timezoneId,
  } = ws;

  const showExtendedHours = chartSettings?.extendedHours !== false;

  // The chart asks for clock-aligned derived intervals (TVP-2.5); strategies keep count mode.
  const chartQuery = useQuery({
    queryKey: ['trading', 'bars', instrumentId, bindingId, interval, historyLimit, 'clock', showExtendedHours],
    queryFn: () => tradingApi.bars(instrumentId, interval, historyLimit, bindingId, { alignment: 'clock', extendedHours: showExtendedHours }),
    enabled: Boolean(instrumentId),
    staleTime: 15_000,
  });

  const comparisonQueries = useQueries({
    queries: comparisons.map((comparison) => {
      const comparisonLimit = chartHistoryLimit(comparison.instrumentId, interval, []);
      return {
        queryKey: comparisonBarsQueryKey(comparison.instrumentId, interval, comparisonLimit, showExtendedHours),
        queryFn: () => comparisonBars(comparison.instrumentId, interval, comparisonLimit, showExtendedHours),
        enabled: Boolean(comparison.instrumentId),
        staleTime: 15_000,
      };
    }),
  });

  const comparisonRenderData = useMemo<TradingComparisonData[]>(() => comparisons.map((comparison, index) => {
    const result = comparisonQueries[index];
    return {
      instrumentId: comparison.instrumentId,
      label: comparisonLabel(result?.data?.instrument, comparison.instrumentId),
      placement: comparison.placement,
      color: TRADING_COMPARISON_COLORS[index % TRADING_COMPARISON_COLORS.length],
      visible: comparison.visible !== false,
      bars: (result?.data?.bars ?? []) as MarketBar[],
    };
  }), [comparisons, comparisonQueries]);

  const sourceCurrency = chartQuery.data?.instrument.quote_currency?.toUpperCase() ?? 'USD';

  const supportsCurrencyConversion = /^[A-Z]{3}$/u.test(sourceCurrency);

  const currencyRateQuery = useQuery({
    queryKey: ['trading', 'currency-rate', sourceCurrency, priceScaleCurrency],
    queryFn: () => tradingApi.currencyRate(sourceCurrency, priceScaleCurrency),
    enabled: supportsCurrencyConversion
      && Boolean(sourceCurrency && priceScaleCurrency && sourceCurrency !== priceScaleCurrency),
    staleTime: 5 * 60_000,
    retry: 1,
  });

  const priceScaleMultiplier = !supportsCurrencyConversion || sourceCurrency === priceScaleCurrency
    ? 1
    : currencyRateQuery.data?.rate ?? 1;

  const selectedTimezone = resolveTradingTimezone(timezoneId, chartQuery.data?.instrument.exchange_timezone);

  useEffect(() => {
    setPriceScaleCurrency(sourceCurrency);
  }, [sourceCurrency, instrumentId, setPriceScaleCurrency]);

  useEffect(() => {
    if (!adapter) return;
    adapter.setPriceScaleMultiplier(priceScaleMultiplier);
  }, [adapter, priceScaleMultiplier]);

  useEffect(() => {
    if (!adapter) return;
    adapter.setComparisonData(comparisonRenderData);
  }, [adapter, comparisonRenderData]);

  useEffect(() => {
    if (!adapter) return;
    adapter.setRightOffset(rightOffset);
  }, [adapter, rightOffset]);

  useEffect(() => {
    if (!adapter) {
      setSelectedIndicator(null);
      return;
    }
    const unregister = adapter.onIndicatorClick((selection) => {
      onActivateRef.current();
      setSelectedIndicator(selection);
    });
    return () => {
      unregister();
      setSelectedIndicator(null);
    };
  }, [adapter, onActivateRef, setSelectedIndicator]);

  useEffect(() => {
    if (selectedIndicator && !indicators.some((indicator) => indicator.id === selectedIndicator.id && indicator.enabled)) {
      setSelectedIndicator(null);
    }
  }, [indicators, selectedIndicator, setSelectedIndicator]);

  useEffect(() => {
    if (!selectedIndicator) return;
    const handleOutsidePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Element && target.closest('.trading-indicator-object-toolbar')) return;
      setSelectedIndicator(null);
    };
    document.addEventListener('pointerdown', handleOutsidePointerDown, true);
    return () => document.removeEventListener('pointerdown', handleOutsidePointerDown, true);
  }, [selectedIndicator, setSelectedIndicator]);

  // Normalized once per load; replay and the chart's data effect share it. Hidden
  // extended hours (TVP-2.5) drop pre- and post-market bars before either sees them.
  const loadedBars = useMemo(
    () => normalizeChartBars(filterExtendedHours((chartQuery.data?.bars ?? []) as MarketBar[], showExtendedHours)),
    [chartQuery.data, showExtendedHours],
  );

  const { refetch: refetchBars } = chartQuery;

  const reloadBars = useCallback(() => { void refetchBars(); }, [refetchBars]);

  const replay = useChartReplayClock({
    active,
    replayMode,
    bars: loadedBars,
    chartKey: `${instrumentId}|${bindingId ?? ''}|${interval}`,
    bindingId: chartQuery.data?.binding.binding_id ?? bindingId ?? null,
    reloadBars,
    instrumentId,
    interval,
    showExtendedHours,
  });

  return {
    chartQuery, comparisonQueries, comparisonRenderData, sourceCurrency, supportsCurrencyConversion,
    currencyRateQuery, priceScaleMultiplier, selectedTimezone, loadedBars, showExtendedHours, ...replay,
  };
}
