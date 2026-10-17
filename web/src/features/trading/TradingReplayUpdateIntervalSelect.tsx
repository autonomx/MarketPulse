import { replayUpdateIntervals } from './replayClock';
import { intervalCompactLabel } from './tradingIntervals';
import { useTradingReplayStore } from './tradingReplayStore';

/**
 * Replay's update interval (TVP-8.1): bar by bar, or a lower interval that plays each bar as it formed from its
 * intrabar data. Only intervals that fit the chart's are offered; a note says where the intrabar data stops.
 */
export function TradingReplayUpdateIntervalSelect({ interval }: { interval: string }) {
  const updateInterval = useTradingReplayStore((state) => state.updateInterval);
  const setUpdateInterval = useTradingReplayStore((state) => state.setUpdateInterval);
  const note = useTradingReplayStore((state) => state.intrabarNote);
  const options = replayUpdateIntervals(interval);
  if (options.length === 0) return null;
  const value = updateInterval && options.includes(updateInterval) ? updateInterval : '';
  return (
    <>
      <select
        aria-label="Replay update interval"
        title="Update interval: play each bar as it formed, one step per interval"
        value={value}
        onChange={(event) => setUpdateInterval(event.target.value || null)}
      >
        <option value="">Bar</option>
        {options.map((option) => <option key={option} value={option}>{intervalCompactLabel(option)}</option>)}
      </select>
      {value && note ? <small className="trading-replay-note" role="status">{note}</small> : null}
    </>
  );
}
