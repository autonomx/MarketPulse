/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useEffect, useMemo, useState } from 'react';
import { TradingHermesResearchPanel } from './TradingHermesResearchPanel';
import { TradingStrategyIndicatorEvidence } from './TradingStrategyIndicatorEvidence';
import { tradingStrategyApi } from './tradingStrategyApi';
import type {
  GapPullbackTradingStrategyConfig,
  HistoricalUniverseMode,
  StrategyRangeBacktestProgress,
  StrategyEvent,
  StrategyRangeBacktestResult,
} from './tradingStrategyTypes';
import { startTicker } from '../../shared/timers';
import { PRODUCT_NAME } from '../../shared/productIdentity';

function isoDate(offsetDays = 0): string {
  const value = new Date();
  value.setDate(value.getDate() + offsetDays);
  return value.toISOString().slice(0, 10);
}

function numeric(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function money(value: string | number | null | undefined): string {
  const parsed = numeric(value);
  return parsed === null
    ? 'N/A'
    : parsed.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
}

function ratio(value: string | number | null | undefined, digits = 2): string {
  const parsed = numeric(value);
  return parsed === null ? 'N/A' : parsed.toFixed(digits);
}

function modeLabel(mode: HistoricalUniverseMode): string {
  if (mode === 'captured_only') return 'Captured only (exact)';
  if (mode === 'reconstructed_only') return 'Reconstruct only (approximate)';
  return 'Captured, reconstruct missing';
}

async function waitForBacktest(
  strategyId: string,
  runId: string,
  onProgress: (progress: StrategyRangeBacktestProgress) => void,
): Promise<StrategyRangeBacktestResult> {
  while (true) {
    const progress = await tradingStrategyApi.backtestRangeProgress(strategyId, runId);
    onProgress(progress);
    if (progress.status === 'completed' && progress.result) return progress.result;
    if (progress.status === 'failed') throw new Error(progress.error || 'Backtest failed.');
    await new Promise((resolve) => window.setTimeout(resolve, 500));
  }
}

export function TradingStrategyBacktest({ strategy }: { strategy: GapPullbackTradingStrategyConfig }) {
  const [startDate, setStartDate] = useState(() => isoDate(-14));
  const [endDate, setEndDate] = useState(() => isoDate(0));
  const [initialCash, setInitialCash] = useState('100000');
  const [spreadBps, setSpreadBps] = useState('40');
  const [scanTimeEt, setScanTimeEt] = useState((strategy.config.universe_scan_time_et ?? '09:20:00').slice(0, 5));
  const [universeMode, setUniverseMode] = useState<HistoricalUniverseMode>('captured_or_reconstructed');
  const [reconstructionMaxAgeDays, setReconstructionMaxAgeDays] = useState('30');
  const [running, setRunning] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [progress, setProgress] = useState<StrategyRangeBacktestProgress | null>(null);
  const [notice, setNotice] = useState('Backtest the saved strategy configuration across a date range.');
  const [result, setResult] = useState<StrategyRangeBacktestResult | null>(null);
  const [indicatorEvents, setIndicatorEvents] = useState<StrategyEvent[]>([]);
  const [indicatorEventsError, setIndicatorEventsError] = useState<string | null>(null);

  useEffect(() => {
    if (!running) {
      setElapsedSeconds(0);
      return;
    }
    const startedAt = Date.now();
    return startTicker(() => setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000)), 1000);
  }, [running]);

  useEffect(() => {
    setScanTimeEt((strategy.config.universe_scan_time_et ?? '09:20:00').slice(0, 5));
    setResult(null);
    setProgress(null);
    setNotice('Backtest the saved strategy configuration across a date range.');
  }, [strategy.strategy_id, strategy.revision]);

  useEffect(() => {
    let alive = true;
    setIndicatorEvents([]);
    setIndicatorEventsError(null);
    if (strategy.config.strategy_version !== '2.0.0') return () => { alive = false; };
    void tradingStrategyApi.events(strategy.strategy_id).then((next) => {
      if (alive) setIndicatorEvents(next);
    }).catch((error) => {
      if (alive) setIndicatorEventsError(error instanceof Error ? error.message : String(error));
    });
    return () => { alive = false; };
  }, [strategy.strategy_id, strategy.revision, strategy.config.strategy_version]);

  const run = async () => {
    if (!startDate || !endDate) {
      setNotice('Choose both a start and end date.');
      return;
    }
    if (endDate < startDate) {
      setNotice('End date must be on or after start date.');
      return;
    }
    setRunning(true);
    setResult(null);
    setProgress(null);
    setNotice('Running strategy-specific daily universe backtest…');
    try {
      const accepted = await tradingStrategyApi.backtestRange(strategy.strategy_id, {
        start_date: startDate,
        end_date: endDate,
        initial_cash: initialCash,
        assumed_spread_bps: spreadBps,
        // Kept for the older API contract; the backtest exits on stop, target,
        // RSI cross, or end-of-session safety flattening instead.
        max_hold_minutes: 390,
        universe_scan_time_et: scanTimeEt ? `${scanTimeEt}:00` : null,
        universe_mode: universeMode,
        reconstruction_max_age_days: Number(reconstructionMaxAgeDays),
        max_sessions: 60,
      });
      setProgress({
        run_id: accepted.run_id,
        strategy_id: strategy.strategy_id,
        status: accepted.status,
        completed_sessions: 0,
        total_sessions: accepted.total_sessions,
        percent: 0,
        current_session: null,
        error: null,
        result: null,
      });
      const next = await waitForBacktest(strategy.strategy_id, accepted.run_id, setProgress);
      setResult(next);
      const incomplete = next.missing_universe_sessions + next.data_unavailable_sessions + next.error_sessions;
      if (next.covered_sessions === 0) {
        setNotice(`Backtest unavailable: 0/${next.requested_trading_sessions} sessions had usable historical universe/data coverage. Return and expectancy are N/A, not zero.`);
      } else if (incomplete) {
        setNotice(`Backtest completed with ${next.covered_sessions}/${next.requested_trading_sessions} sessions covered (${next.exact_sessions} exact, ${next.reconstructed_sessions} reconstructed). Missing/unavailable days are excluded rather than treated as no-trade days.`);
      } else {
        setNotice(`Backtest completed across all ${next.covered_sessions} sessions · ${next.result_quality} historical fidelity.`);
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setRunning(false);
    }
  };

  const coverageTone = useMemo(() => {
    if (!result) return 'idle';
    if (result.covered_sessions === 0) return 'attention';
    return result.covered_sessions === result.requested_trading_sessions ? 'complete' : 'attention';
  }, [result]);

  return (
    <>
      <section className="strategy-range-backtest" aria-label="Strategy-specific backtest">
        <header>
          <div>
            <strong>Backtest this strategy</strong>
            <small>Replay the saved {strategy.strategy_kind} config and risk profile day by day, including historical candidate discovery, deterministic selection and portfolio limits.</small>
          </div>
          <span>{strategy.config.structure_interval} structure / {strategy.config.execution_interval} execution</span>
        </header>

        <div className="strategy-backtest-controls">
          <label><span>Start date</span><input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
          <label><span>End date</span><input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
          <label><span>Initial cash</span><input type="number" min="1" step="1000" value={initialCash} onChange={(event) => setInitialCash(event.target.value)} /></label>
          <label><span>Assumed spread<small>bps; also used when historical spread is unavailable</small></span><input type="number" min="0" step="5" value={spreadBps} onChange={(event) => setSpreadBps(event.target.value)} /></label>
          <label><span>Historical scan time ET<small>separate from the 09:35 entry window</small></span><input type="time" value={scanTimeEt} onChange={(event) => setScanTimeEt(event.target.value)} /></label>
          <label><span>Historical universe<small>{modeLabel(universeMode)}</small></span><select value={universeMode} onChange={(event) => setUniverseMode(event.target.value as HistoricalUniverseMode)}><option value="captured_or_reconstructed">Captured, reconstruct missing</option><option value="captured_only">Captured only (exact)</option><option value="reconstructed_only">Reconstruct only (approximate)</option></select></label>
          <label><span>Reconstruction age<small>maximum calendar days</small></span><input type="number" min="1" max="3650" value={reconstructionMaxAgeDays} onChange={(event) => setReconstructionMaxAgeDays(event.target.value)} disabled={universeMode === 'captured_only'} /></label>
          <button type="button" className="primary" disabled={running} onClick={() => void run()}>{running ? 'Running backtest…' : 'Run strategy backtest'}</button>
        </div>

        <div className="strategy-backtest-causality-note">
          <strong>Historical evidence modes</strong>
          <span><b>Captured</b> uses an immutable universe {PRODUCT_NAME} actually froze by the configured scan time and replays the full saved strategy. <b>Reconstructed</b> is explicitly approximate: for recent missing days it rebuilds the morning gap/price/volume/RVOL candidate set from Alpaca IEX historical data and today&apos;s active listing universe. Because point-in-time catalyst, dilution and float history is unavailable, those hard evidence gates are relaxed only for reconstructed sessions and every downgrade is reported in the result. Reconstructed output must not be interpreted as equivalent to captured point-in-time evidence.</span>
        </div>
        <p className="strategy-backtest-notice" role="status">{notice}</p>
        {running ? (
          <div className="strategy-backtest-progress" role="status" aria-live="polite">
            <div className="strategy-backtest-progress-header">
              <strong>Backtest in progress · {progress?.percent ?? 0}% complete</strong>
              <span>{elapsedSeconds}s elapsed</span>
            </div>
            <div
              className="strategy-backtest-progress-track"
              data-indeterminate={progress?.status === 'queued' ? 'true' : undefined}
              role="progressbar"
              aria-label="Backtest progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progress?.percent ?? 0}
              aria-valuetext={`${progress?.percent ?? 0}% complete`}
            >
              <span style={{ width: `${progress?.percent ?? 0}%` }} />
            </div>
            <small>{progress?.completed_sessions ?? 0}/{progress?.total_sessions ?? '—'} trading sessions completed. Processing historical sessions and applying the saved strategy and risk rules.</small>
          </div>
        ) : null}

        {result ? (
          <>
            <div className="strategy-backtest-summary" data-coverage={coverageTone}>
              <article><small>Coverage</small><strong>{result.covered_sessions}/{result.requested_trading_sessions}</strong><span>{result.exact_sessions} exact · {result.reconstructed_sessions} reconstructed · {result.no_candidate_sessions} valid no-candidate days</span></article>
              <article><small>Result quality</small><strong>{result.result_quality}</strong><span>{result.missing_universe_sessions} missing universe · {result.data_unavailable_sessions} unavailable data · {result.error_sessions} errors</span></article>
              <article><small>Ending equity</small><strong>{result.covered_sessions ? money(result.ending_cash) : 'N/A'}</strong><span>{money(result.pnl)} · {ratio(result.return_pct)}{result.return_pct === null ? '' : '%'}</span></article>
              <article><small>Trades</small><strong>{result.trade_count}</strong><span>{result.win_count} wins · {result.loss_count} losses · {result.trigger_count} triggers</span></article>
              <article><small>Expectancy</small><strong>{result.expectancy_r === null ? 'N/A' : `${ratio(result.expectancy_r)}R`}</strong><span>{result.candidate_count} candidates evaluated in covered sessions</span></article>
            </div>

            <div className="strategy-backtest-days-wrap">
              <table className="strategy-backtest-days">
                <thead><tr><th>Date</th><th>Status</th><th>Source / fidelity</th><th>Universe</th><th>Candidates</th><th>Triggers</th><th>Trades</th><th>P&amp;L</th><th>Ending cash</th></tr></thead>
                <tbody>
                  {result.days.map((day) => (
                    <tr key={day.session_date} data-status={day.status} title={[day.detail, ...day.fidelity_warnings, ...day.strategy_fidelity_adjustments].filter(Boolean).join('\n') || undefined}>
                      <td>{day.session_date}</td>
                      <td><strong>{day.status.replaceAll('_', ' ')}</strong>{day.detail ? <small>{day.detail}</small> : null}</td>
                      <td><strong>{day.universe_origin ?? '—'}</strong><small>{day.fidelity ?? '—'}{day.strategy_fidelity_adjustments.length ? ` · ${day.strategy_fidelity_adjustments.length} fidelity adjustments` : ''}</small></td>
                      <td>{day.universe_id ?? '—'}</td>
                      <td>{day.candidate_count}</td>
                      <td>{day.trigger_count}</td>
                      <td>{day.trade_count}</td>
                      <td>{day.status === 'backtested' || day.status === 'no_candidates' ? money(day.pnl) : 'N/A'}</td>
                      <td>{day.status === 'backtested' || day.status === 'no_candidates' ? money(day.ending_cash) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <details className="strategy-backtest-trades">
              <summary>Trade-by-trade detail</summary>
              <div>
                {result.days.flatMap((day) => (day.result?.trades ?? []).map((trade, index) => (
                  <article key={`${day.session_date}-${trade.instrument_id}-${trade.entry_time}-${index}`}>
                    <header><strong>{trade.instrument_id.split(':').at(-1) ?? trade.instrument_id}</strong><span>{day.session_date} · {day.universe_origin ?? 'unknown'} fidelity</span><b>{ratio(trade.r_multiple)}R</b></header>
                    <small>score {trade.quality_score}/10 · entry {money(trade.entry_price)} · exit {money(trade.exit_price)} · {trade.exit_reason} · qty {ratio(trade.entry_fill_quantity, 0)}</small>
                  </article>
                )))}
                {!result.trade_count && result.covered_sessions ? <p>No trades were executed in covered sessions.</p> : null}
                {!result.covered_sessions ? <p>No sessions were actually backtested. Performance metrics are unavailable rather than zero.</p> : null}
              </div>
            </details>
          </>
        ) : null}
      </section>
      <TradingStrategyIndicatorEvidence
        events={indicatorEvents}
        visible={strategy.config.strategy_version === '2.0.0'}
        loadError={indicatorEventsError}
      />
      <TradingHermesResearchPanel strategy={strategy} />
    </>
  );
}
