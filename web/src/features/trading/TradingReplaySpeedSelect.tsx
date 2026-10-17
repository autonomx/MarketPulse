import { formatReplaySpeed, parseReplaySpeed, REPLAY_SPEEDS } from './replayClock';
import { useTradingReplayStore } from './tradingReplayStore';

/** The one replay speed control; chart replay and frozen-dataset replay share its setting. */
export function TradingReplaySpeedSelect() {
  const speed = useTradingReplayStore((state) => state.speed);
  const setSpeed = useTradingReplayStore((state) => state.setSpeed);
  return (
    <select
      aria-label="Replay speed"
      title="Replay speed: bars per second"
      value={String(speed)}
      onChange={(event) => setSpeed(parseReplaySpeed(event.target.value))}
    >
      {REPLAY_SPEEDS.map((option) => (
        <option key={option} value={String(option)}>{formatReplaySpeed(option)}</option>
      ))}
    </select>
  );
}
