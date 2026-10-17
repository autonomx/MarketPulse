import { formatTradingTime, formatTradingTimezoneOffset, resolveTradingTimezone, TRADING_TIMEZONE_OPTIONS, writeTradingTimezoneId } from './tradingTime';
import { ranges } from './tradingChartPanelModel';
import { TradingReplaySpeedSelect } from './TradingReplaySpeedSelect';
import { TradingReplayUpdateIntervalSelect } from './TradingReplayUpdateIntervalSelect';
import type { TradingChartPanelModel } from './useTradingChartPanel';
import { emitOmnixEvent, TRADING_CHART_TIMEZONE_CHANGE_EVENT } from '../../events/bus';
import { useNow } from '../../shared/timers';
import { ChartGoToDate } from './TradingChartWorkflowControls';

/** The footer: replay controls, visible ranges, timezone, offset and clock. */
export function ChartPanelFooter({ ws }: { ws: TradingChartPanelModel }) {
  const {
    active, allBarsRef, applyCustomRange, chartId, chartQuery, customRangeEnd, customRangeError, customRangeOpen,
    customRangeRef, customRangeStart, drawings, exitReplay, interval, nextReplayBar, openCustomRange,
    previousReplayBar, provenance, replayChoosingStart, replayCurrentBar, replayHasNextBar, replayHasPreviousBar,
    replayMode, replayPlaying, replayStartTime, replayVisibleBarCount, resetReplay, selectReplayStart,
    selectedRangeLabel, selectedTimezone, selectedTimezoneOption, setCustomRangeEnd, setCustomRangeOpen,
    setCustomRangeStart, setTimezoneId, setTimezoneMenuOpen, showRange, streamStatus, timezoneId, timezoneMenuOpen,
    timezoneMenuRef, toggleReplayPlaying,
  } = ws;
  return (
    <>
      <footer>
        {replayMode && active ? (
          <div className="trading-replay-toolbar" role="group" aria-label="Chart replay controls" onPointerDown={(event) => event.stopPropagation()}>
            <button type="button" onClick={selectReplayStart} disabled={replayChoosingStart} aria-label="Choose replay start" title="Jump to a new start bar">Select bar</button>
            <button type="button" onClick={resetReplay} disabled={replayStartTime === null} aria-label="Reset replay" title="Reset replay">↤</button>
            <button type="button" onClick={previousReplayBar} disabled={!replayHasPreviousBar} aria-label="Replay previous bar" title="Previous bar">|‹</button>
            <button type="button" className="trading-replay-play" onClick={toggleReplayPlaying} disabled={!replayHasNextBar} aria-label={replayPlaying ? 'Pause replay' : 'Play replay'} title={replayPlaying ? 'Pause replay' : 'Play replay'}>{replayPlaying ? 'Ⅱ' : '▶'}</button>
            <button type="button" onClick={nextReplayBar} disabled={!replayHasNextBar} aria-label="Replay next bar" title="Replay next bar">›|</button>
            <TradingReplaySpeedSelect />
            <TradingReplayUpdateIntervalSelect interval={interval} />
            <span className="trading-replay-progress">{replayCurrentBar ? new Date(replayCurrentBar.end_time).toLocaleDateString() : 'Select a bar'} · {replayVisibleBarCount}/{allBarsRef.current.length}</span>
            <button type="button" className="trading-replay-exit" onClick={exitReplay} aria-label="Exit replay and return to real time" title="Exit replay and return to real time">Real time</button>
          </div>
        ) : null}
        <nav aria-label={`${chartId} visible range`} onPointerDown={(event) => event.stopPropagation()}>
          {ranges.map((range) => (
            <button
              key={range.label}
              type="button"
              aria-pressed={selectedRangeLabel === range.label}
              aria-label={`${range.label}: ${range.tooltip}`}
              data-tooltip={range.tooltip}
              onClick={() => showRange(range.label, range.days, range.interval)}
            >
              {range.label}
            </button>
          ))}
          <div className="trading-custom-range" ref={customRangeRef}>
            <button
              type="button"
              className="trading-custom-range-trigger"
              aria-label="Select custom date range"
              aria-expanded={customRangeOpen}
              aria-pressed={selectedRangeLabel === 'Custom'}
              title="Custom date range"
              onClick={openCustomRange}
            >
              <svg aria-hidden="true" viewBox="0 0 16 16" focusable="false">
                <rect x="2.5" y="3.5" width="11" height="10" rx="1" />
                <path d="M5 2v3M11 2v3M2.5 6.5h11" />
              </svg>
            </button>
            {customRangeOpen ? (
              <form className="trading-custom-range-popover" onSubmit={(event) => { event.preventDefault(); applyCustomRange(); }}>
                <strong>Custom range</strong>
                <label>From<input type="date" value={customRangeStart} onChange={(event) => setCustomRangeStart(event.target.value)} /></label>
                <label>To<input type="date" value={customRangeEnd} onChange={(event) => setCustomRangeEnd(event.target.value)} /></label>
                {customRangeError ? <small role="alert">{customRangeError}</small> : null}
                <div>
                  <button type="button" onClick={() => setCustomRangeOpen(false)}>Cancel</button>
                  <button type="submit" className="primary">Apply</button>
                </div>
              </form>
            ) : null}
          </div>
          <ChartGoToDate ws={ws} />
        </nav>
        <div className="trading-chart-footer-meta">
          <div className="trading-timezone-control" ref={timezoneMenuRef}>
            <button
              type="button"
              className="trading-timezone-trigger"
              aria-label={`Chart timezone: ${selectedTimezoneOption.label}`}
              aria-haspopup="listbox"
              aria-expanded={timezoneMenuOpen}
              title={`Timezone: ${selectedTimezoneOption.label}`}
              onClick={() => { setTimezoneMenuOpen((current) => !current); setCustomRangeOpen(false); }}
            >
              <TradingClock timezone={selectedTimezone} />
            </button>
            {timezoneMenuOpen ? (
              <div className="trading-timezone-menu" role="listbox" aria-label="Chart timezone">
                {TRADING_TIMEZONE_OPTIONS.map((option) => {
                  const optionTimezone = resolveTradingTimezone(option.id, chartQuery.data?.instrument.exchange_timezone);
                  const offset = formatTradingTimezoneOffset(new Date(), optionTimezone);
                  return (
                    <button
                      key={option.id}
                      type="button"
                      role="option"
                      aria-selected={timezoneId === option.id}
                      onClick={() => {
                        setTimezoneId(option.id);
                        writeTradingTimezoneId(option.id);
                        emitOmnixEvent(TRADING_CHART_TIMEZONE_CHANGE_EVENT, option.id);
                        setTimezoneMenuOpen(false);
                      }}
                    >
                      <span>{option.label}</span>
                      <small>{offset}</small>
                      {timezoneId === option.id ? <b aria-hidden="true">✓</b> : null}
                    </button>
                  );
                })}
              </div>
            ) : null}
          </div>
          <span>{streamStatus === 'live' ? 'live source' : provenance?.cached ? 'cached' : 'live source'}</span>
          <span>{drawings.status}</span>
        </div>
      </footer>
    </>
  );
}

/** The chart footer's clock; it re-renders itself each second, not the chart panel (WP-9.9). */
function TradingClock({ timezone }: { timezone: string }) {
  const now = useNow(1_000);
  return <>{`${formatTradingTime(now, timezone)} ${formatTradingTimezoneOffset(now, timezone)}`}</>;
}
