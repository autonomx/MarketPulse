/** Chart workflow UI of TVP-2.5: bar-close countdown, go-to-date box, chart settings additions and legend badges. */
import { useEffect, useEffectEvent, useRef, useState, type FormEvent } from 'react';
import type { TradingChartAdapter } from './chart/chartAdapter';
import { barCountdownRemainingMs, formatBarCountdown, type ChartTemplate } from './tradingChartWorkflow';
import type { MarketBar } from './tradingTypes';
import type { TradingChartPanelModel } from './useTradingChartPanel';
import { useNow } from '../../shared/timers';
import './TradingChartWorkflow.css';

/** The time left in the latest bar, drawn under the last-price label on the price scale. */
export function TradingBarCountdown({
  adapter,
  bar,
  interval,
}: {
  adapter: TradingChartAdapter;
  bar: MarketBar | undefined;
  interval: string;
}) {
  const now = useNow(1_000);
  const [, setViewportRevision] = useState(0);
  useEffect(() => adapter.onViewportChange(() => setViewportRevision((value) => value + 1)), [adapter]);
  const remaining = barCountdownRemainingMs(bar, interval, now.getTime());
  if (remaining === null) return null;
  let position: ReturnType<TradingChartAdapter['lastPriceLabelPosition']> = null;
  try {
    position = adapter.lastPriceLabelPosition();
  } catch {
    // The adapter may be disposed while the chart switches symbol.
    return null;
  }
  if (!position) return null;
  const text = formatBarCountdown(remaining);
  return (
    <div
      className="trading-bar-countdown"
      role="timer"
      aria-label={`Bar closes in ${text}`}
      style={{
        top: Math.min(position.paneHeight - 16, position.y + 10),
        [position.side]: 0,
        width: position.scaleWidth,
        background: position.color,
      }}
    >
      {text}
    </div>
  );
}

/** The go-to-date button and popover in the chart footer. */
export function ChartGoToDate({ ws }: { ws: TradingChartPanelModel }) {
  const { goToDate, goToDateDefault, goToDateError, goToDateLoading, goToDateOpen, openGoToDate, replayMode, setGoToDateOpen } = ws;
  const [value, setValue] = useState('');
  const rootRef = useRef<HTMLDivElement | null>(null);
  // Seeds the box when it opens; later edits are the user's.
  const seed = useEffectEvent(() => setValue((current) => current || goToDateDefault()));
  const closeOutside = useEffectEvent((event: PointerEvent) => {
    if (event.target instanceof Node && !rootRef.current?.contains(event.target)) setGoToDateOpen(false);
  });
  useEffect(() => {
    if (!goToDateOpen) return;
    seed();
    const close = (event: PointerEvent) => closeOutside(event);
    document.addEventListener('pointerdown', close, true);
    return () => document.removeEventListener('pointerdown', close, true);
  }, [goToDateOpen]);
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    goToDate(value);
  };
  return (
    <div className="trading-go-to-date" ref={rootRef}>
      <button
        type="button"
        className="trading-go-to-date-trigger"
        aria-label="Go to date"
        aria-expanded={goToDateOpen}
        title="Go to date"
        disabled={replayMode}
        onClick={() => goToDateOpen ? setGoToDateOpen(false) : openGoToDate()}
      >
        <svg aria-hidden="true" viewBox="0 0 16 16" focusable="false">
          <rect x="2.5" y="3.5" width="11" height="10" rx="1" />
          <path d="M5 2v3M11 2v3M2.5 6.5h11M6 10h4M8.5 8.5 10 10l-1.5 1.5" />
        </svg>
      </button>
      {goToDateOpen ? (
        <form className="trading-custom-range-popover trading-go-to-date-popover" aria-label="Go to date" onSubmit={submit}>
          <strong>Go to</strong>
          <label>Date<input type="date" value={value.slice(0, 10)} onChange={(event) => setValue(`${event.target.value}${value.slice(10)}`)} /></label>
          <label>Time<input type="time" value={value.slice(11, 16)} onChange={(event) => setValue(event.target.value ? `${value.slice(0, 10)}T${event.target.value}` : value.slice(0, 10))} /></label>
          {goToDateLoading ? <small role="status">Loading older history…</small> : null}
          {goToDateError ? <small role="alert">{goToDateError}</small> : null}
          <div>
            <button type="button" onClick={() => setGoToDateOpen(false)}>Cancel</button>
            <button type="submit" className="primary" disabled={!value || goToDateLoading}>Go to</button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

/** Copy-to-clipboard button for the chart header's tools. */
export function ChartCopyImageButton({ ws }: { ws: TradingChartPanelModel }) {
  const { chartImageCopyStatus, copyChartImage } = ws;
  const label = chartImageCopyStatus === 'copied'
    ? 'Copied'
    : chartImageCopyStatus === 'error' ? 'Copy failed' : 'Copy';
  return (
    <button
      type="button"
      onClick={() => void copyChartImage()}
      aria-label="Copy chart image"
      title={chartImageCopyStatus === 'error' ? 'This browser could not copy the image' : 'Copy chart image to the clipboard'}
      disabled={chartImageCopyStatus === 'copying'}
    >
      {label}
    </button>
  );
}

/** Copies a link to a stored snapshot of the chart (TVP-2.5; Alt+S). */
export function ChartSnapshotLinkButton({ ws }: { ws: TradingChartPanelModel }) {
  const { snapshotLinkStatus, copySnapshotLink } = ws;
  const label = snapshotLinkStatus === 'copied' ? 'Link copied' : snapshotLinkStatus === 'error' ? 'Link failed' : snapshotLinkStatus === 'copying' ? 'Saving…' : 'Link';
  return (
    <button
      type="button"
      onClick={() => void copySnapshotLink()}
      aria-label="Copy chart snapshot link"
      title="Save a snapshot of the chart and copy its link (Alt+S); the link opens for signed-in members of this workspace"
      disabled={snapshotLinkStatus === 'copying'}
    >
      {label}
    </button>
  );
}

/** Market session and delayed-data badges beside the symbol. */
export function ChartMarketStatusBadges({ ws }: { ws: TradingChartPanelModel }) {
  const { active, dataDelay, marketStatus, marketStatusValue } = ws;
  if (!marketStatus && !dataDelay) return null;
  // Only the active chart's badge is a live region, so the workspace has one, not one per chart.
  return (
    <span className="trading-market-badges">
      {marketStatus ? (
        <span className="trading-market-status" data-status={marketStatusValue ?? undefined} role={active ? 'status' : undefined} title={marketStatus}>
          <i aria-hidden="true" />{marketStatus}
        </span>
      ) : null}
      {dataDelay ? <span className="trading-data-delay" title="The feed reports delayed data">{dataDelay}</span> : null}
    </span>
  );
}

/** Chart settings added by TVP-2.5: countdown, extended hours, pre/post-market line and templates. */
export function ChartWorkflowSettings({ ws }: { ws: TradingChartPanelModel }) {
  const {
    applyChartTemplateRecord, barCountdownAvailable, barCountdownOn, chartSettings, chartTemplateStatus, chartTemplates,
    deleteChartTemplate, eventMarkersAvailable, eventMarkersOn, extendedHoursAvailable, loadChartTemplates, saveChartTemplate, sessionTaggedBars,
    showExtendedHours, updateChartSettings,
  } = ws;
  const extendedReason = !sessionTaggedBars
    ? 'This feed sends bars without exchange sessions, so there are no pre/post-market bars to hide.'
    : showExtendedHours && !extendedHoursAvailable ? 'This feed sent no pre/post-market bars.' : null;
  const [templateName, setTemplateName] = useState('');
  useEffect(() => { loadChartTemplates(); }, [loadChartTemplates]);
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (await saveChartTemplate(templateName || 'Chart template')) setTemplateName('');
  };
  return (
    <>
      <label className="trading-chart-setting-toggle">
        <input
          type="checkbox"
          checked={barCountdownOn}
          disabled={!barCountdownAvailable}
          onChange={(event) => updateChartSettings({ barCountdown: event.target.checked })}
        />
        Countdown to bar close
      </label>
      <label className="trading-chart-setting-toggle" title={extendedReason ?? undefined}>
        <input
          type="checkbox"
          checked={showExtendedHours}
          disabled={!sessionTaggedBars}
          aria-describedby={extendedReason ? `${ws.chartId}-extended-hours-reason` : undefined}
          onChange={(event) => updateChartSettings({ extendedHours: event.target.checked })}
        />
        Extended hours (pre/post-market)
      </label>
      {extendedReason ? <small id={`${ws.chartId}-extended-hours-reason`} className="trading-chart-setting-reason">{extendedReason}</small> : null}
      <label className="trading-chart-setting-toggle">
        <input
          type="checkbox"
          checked={chartSettings?.extendedPriceLine !== false}
          onChange={(event) => updateChartSettings({ extendedPriceLine: event.target.checked })}
        />
        Pre/post-market price line
      </label>
      {eventMarkersAvailable ? (
        <label className="trading-chart-setting-toggle">
          <input type="checkbox" checked={eventMarkersOn} onChange={(event) => updateChartSettings({ events: event.target.checked })} />
          Earnings, dividends and splits
        </label>
      ) : null}
      <section className="trading-chart-templates" aria-label="Chart templates">
        <strong>Templates</strong>
        <small>Style and indicators, without the symbol.</small>
        <form onSubmit={(event) => void save(event)}>
          <input aria-label="Template name" placeholder="Template name" value={templateName} onChange={(event) => setTemplateName(event.target.value)} />
          <button type="submit" disabled={chartTemplateStatus === 'saving'}>Save</button>
        </form>
        {chartTemplateStatus === 'error' ? <small role="alert">Template could not be saved.</small> : null}
        <ul>
          {chartTemplates.map((template: ChartTemplate) => (
            <li key={template.recordId}>
              <button type="button" onClick={() => applyChartTemplateRecord(template)} title={`Apply ${template.name}`}>{template.name}</button>
              <button type="button" aria-label={`Delete template ${template.name}`} onClick={() => void deleteChartTemplate(template)}>×</button>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
