/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useEffect, useMemo, useState } from 'react';
import type { BacktestRunResult, FrozenDatasetSnapshot } from './replayTypes';
import { replayTickPlan } from './replayClock';
import { TradingReplaySpeedSelect } from './TradingReplaySpeedSelect';
import { tradingReplayApi } from './tradingReplayApi';
import { useTradingReplayStore } from './tradingReplayStore';
import { startTicker } from '../../shared/timers';

export function TradingReplayPanel({
  instrumentId,
  bindingId,
  interval,
}: {
  instrumentId: string;
  bindingId: string | null;
  interval: string;
}) {
  const [datasets, setDatasets] = useState<FrozenDatasetSnapshot[]>([]);
  const [selectedDatasetId, setSelectedDatasetId] = useState('');
  const [backtest, setBacktest] = useState<BacktestRunResult | null>(null);
  const [replayIndex, setReplayIndex] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const speed = useTradingReplayStore((state) => state.speed);
  const [fast, setFast] = useState('10');
  const [slow, setSlow] = useState('30');
  const [allowShort, setAllowShort] = useState(false);
  const [status, setStatus] = useState<'loading' | 'ready' | 'saving' | 'error'>('loading');

  const refresh = async () => {
    try {
      const next = await tradingReplayApi.datasets();
      setDatasets(next);
      if (!selectedDatasetId && next[0]) setSelectedDatasetId(next[0].dataset_id);
      setStatus('ready');
    } catch {
      setStatus('error');
    }
  };

  useEffect(() => { void refresh(); }, []);
  const selected = useMemo(
    () => datasets.find((dataset) => dataset.dataset_id === selectedDatasetId) ?? null,
    [datasets, selectedDatasetId],
  );

  useEffect(() => {
    if (!playing || !selected) return;
    const { intervalMs, barsPerTick } = replayTickPlan(speed);
    return startTicker(() => {
      setReplayIndex((current) => {
        if (current + 1 >= selected.bars.length) {
          setPlaying(false);
          return current;
        }
        return Math.min(selected.bars.length - 1, current + barsPerTick);
      });
    }, intervalMs);
  }, [playing, selected, speed]);

  const freeze = async () => {
    setStatus('saving');
    try {
      const dataset = await tradingReplayApi.freeze({
        dataset_id: `dataset-${Date.now()}`,
        instrument_id: instrumentId,
        binding_id: bindingId,
        interval,
        limit: 1_000,
        gap_policy: 'fail',
      });
      await refresh();
      setSelectedDatasetId(dataset.dataset_id);
      setReplayIndex(-1);
    } catch {
      setStatus('error');
    }
  };

  const run = async () => {
    if (!selected) return;
    setStatus('saving');
    try {
      const result = await tradingReplayApi.runBacktest(selected.dataset_id, {
        fast_period: Number(fast),
        slow_period: Number(slow),
        initial_cash: '10000',
        commission_bps: '10',
        slippage_bps: '5',
        allow_short: allowShort,
      });
      setBacktest(result);
      setStatus('ready');
    } catch {
      setStatus('error');
    }
  };

  const replayBar = selected && replayIndex >= 0 ? selected.bars[replayIndex] : null;
  return (
    <section className="trading-replay-panel" aria-label="Frozen replay and backtest" data-status={status}>
      <header><strong>Replay & backtest</strong><span>{status}</span></header>
      <p>Runs use immutable finalized datasets. Live bars and provider corrections cannot enter a frozen replay or backtest.</p>
      <button type="button" onClick={() => void freeze()}>Freeze active chart dataset</button>
      <label>
        Dataset
        <select value={selectedDatasetId} onChange={(event) => { setSelectedDatasetId(event.target.value); setReplayIndex(-1); }}>
          {datasets.map((dataset) => <option key={dataset.dataset_id} value={dataset.dataset_id}>{dataset.instrument_id} · {dataset.interval} · {dataset.dataset_fingerprint.slice(0, 8)}</option>)}
        </select>
      </label>
      {selected ? (
        <div className="trading-replay-controls">
          <button type="button" onClick={() => setPlaying((value) => !value)}>{playing ? 'Pause' : 'Play'}</button>
          <button type="button" onClick={() => setReplayIndex((current) => Math.min(selected.bars.length - 1, current + 1))}>Step</button>
          <button type="button" onClick={() => { setPlaying(false); setReplayIndex(-1); }}>Reset</button>
          <TradingReplaySpeedSelect />
          <span>{Math.max(0, replayIndex + 1)}/{selected.bars.length}</span>
        </div>
      ) : null}
      {replayBar ? <div className="trading-replay-bar"><time>{new Date(replayBar.end_time).toLocaleString()}</time><strong>{replayBar.close}</strong><span>O {replayBar.open} H {replayBar.high} L {replayBar.low} V {replayBar.volume}</span></div> : null}
      <div className="trading-backtest-form">
        <label>Fast SMA<input inputMode="numeric" value={fast} onChange={(event) => setFast(event.target.value)} /></label>
        <label>Slow SMA<input inputMode="numeric" value={slow} onChange={(event) => setSlow(event.target.value)} /></label>
        <label><input type="checkbox" checked={allowShort} onChange={(event) => setAllowShort(event.target.checked)} />Allow short</label>
        <button type="button" disabled={!selected || status === 'saving'} onClick={() => void run()}>Run backtest</button>
      </div>
      {backtest ? (
        <dl role="group" className="trading-backtest-summary" aria-label="Backtest evidence summary">
          <div><dt>Status</dt><dd>{backtest.status}</dd></div>
          <div><dt>Final equity</dt><dd>{backtest.final_equity}</dd></div>
          <div><dt>Return</dt><dd>{backtest.total_return_percent}%</dd></div>
          <div><dt>Max drawdown</dt><dd>{backtest.max_drawdown_percent}%</dd></div>
          <div><dt>Win rate</dt><dd>{backtest.win_rate_percent}%</dd></div>
          <div><dt>Exposure</dt><dd>{backtest.exposure_percent}%</dd></div>
          <div><dt>Trades</dt><dd>{backtest.trade_count}</dd></div>
          <div><dt>Dataset</dt><dd title={backtest.dataset_fingerprint}>{backtest.dataset_fingerprint.slice(0, 10)}</dd></div>
          {backtest.artifact ? (
            <div>
              <dt>Artifact</dt>
              <dd title={backtest.artifact.storage_key}>{backtest.artifact.storage_provider} · {backtest.artifact.checksum_sha256.slice(0, 10)} · {backtest.artifact.byte_size} bytes</dd>
            </div>
          ) : null}
        </dl>
      ) : null}
    </section>
  );
}
