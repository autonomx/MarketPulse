import { useState } from 'react';
import type {
  TradingAlertCondition,
  TradingAlertIndicatorId,
  TradingAlertNotificationChannel,
  TradingAlertTriggerPolicy,
} from './tradingTypes';
import { formatAlertThreshold } from './tradingChartAlerts';
import { MESSAGE_PLACEHOLDERS, type AlertDeliveryEditor } from './alertDelivery';
import { AlertDeliveryFields } from './AlertDeliveryFields';
import { AlertIndicatorPicker } from './AlertIndicatorPicker';
import { resolveIndicatorSelection, type AlertIndicatorChoice, type AlertIndicatorSelection } from './alertIndicatorSources';
import { AlertConditionRows } from './AlertConditionRows';
import { MAX_ALERT_CONDITIONS, acceptsExtraConditions, newConditionDraft, type ConditionDraft, type DraftLine } from './alertConditionDrafts';
import { AlertLineField } from './AlertLineField';
import './TradingChartAlertOpaque.css';

export type TradingAlertEditorState = AlertDeliveryEditor & {
  mode: 'create' | 'edit';
  alertId: string | null;
  x: number;
  y: number;
  condition: TradingAlertCondition;
  threshold: string;
  expiresAt: string;
  expiration: 'never' | '1h' | '1d' | '1w';
  triggerPolicy: TradingAlertTriggerPolicy;
  message: string;
  notifications: TradingAlertNotificationChannel[];
  indicator: TradingAlertIndicatorId;
  period: string;
  lookback: string;
  trendlinePoints?: Array<{ time: string; price: number }>;
  /** A chart indicator, line and comparison (TVP-1.3), when the alert is on one. */
  indicatorSelection?: AlertIndicatorSelection;
  /** A new price alert's line to cross instead of the value (TVP-1.3): a price field or a chart indicator line. */
  priceLine?: DraftLine;
  /** The chart indicator the alert was placed on (its pane), any indicator id. */
  chartIndicatorId?: string;
  /** The drawing a line alert follows, its levels and the chosen one (TVP-1.4). */
  drawingId?: string;
  drawingLevels?: ReadonlyArray<{ key: string; label: string; anchors: readonly [{ time: string; price: number }, { time: string; price: number }] }>;
  drawingLevel?: string;
  /** A conditions alert's summary, shown when its conditions can't all be edited here (a trendline among them). */
  conditionsSummary?: string;
  /**
   * Multi-condition alerts (TVP-1.6): for a conditions alert, all its conditions; otherwise the conditions added after
   * the alert's own one. Combined with AND.
   */
  conditionDrafts?: ConditionDraft[];
  /** False when a conditions alert holds one the dialog can't edit: it is shown as its summary and kept. */
  conditionsEditable?: boolean;
  /** Why the conditions can't be saved (shown until the conditions change). */
  conditionError?: string;
  /** What a new alert applies to: the chart's instrument, or `watchlist:<id>` for every symbol of a list (TVP-1.7). */
  target?: string;
}

/** Whether "Add condition" can add one, or why not. */
function addConditionBlock(editor: TradingAlertEditorState, usesChartIndicators: boolean): string | null {
  const drafts = editor.conditionDrafts ?? [];
  const isConditions = editor.condition === 'conditions';
  if (editor.condition.startsWith('trendline_')) return 'Drawing alerts follow their drawing and take one condition: add other conditions in a separate alert.';
  if (isConditions && editor.conditionsEditable === false) return "This alert has a condition the dialog can't edit, so its conditions are kept as they are.";
  if (!acceptsExtraConditions(editor.condition) || (editor.condition.startsWith('indicator_') && !usesChartIndicators)) {
    return 'This alert takes one condition: add the others in a separate alert.';
  }
  return (isConditions ? drafts.length : drafts.length + 1) >= MAX_ALERT_CONDITIONS ? `An alert takes at most ${MAX_ALERT_CONDITIONS} conditions.` : null;
}

const priceConditionOptions: Array<{ value: TradingAlertCondition; label: string }> = [
  { value: 'price_above', label: 'Price' },
  { value: 'percent_change_above', label: 'Percent change' },
  { value: 'indicator_above', label: 'Indicator' },
  { value: 'volume_above', label: 'Volume' },
];

const trendlineModeOptions: Array<{ value: TradingAlertCondition; label: string }> = [
  { value: 'trendline_crossing', label: 'Crossing' },
  { value: 'trendline_crossing_up', label: 'Crossing Up' },
  { value: 'trendline_crossing_down', label: 'Crossing Down' },
  { value: 'trendline_above', label: 'Greater Than' },
  { value: 'trendline_below', label: 'Less Than' },
];

const triggerOptions: Array<{ value: TradingAlertTriggerPolicy; label: string }> = [
  { value: 'once', label: 'Once only' },
  { value: 'once_per_bar', label: 'Once per bar' },
  { value: 'once_per_bar_close', label: 'Once per bar close' },
  { value: 'once_per_minute', label: 'Once per minute' },
  { value: 'every_time', label: 'Every time' },
];

function conditionFamily(condition: TradingAlertCondition): TradingAlertCondition {
  if (condition.startsWith('trendline_')) return 'trendline_crossing';
  if (condition.startsWith('percent_change_')) return 'percent_change_above';
  if (condition.startsWith('indicator_')) return 'indicator_above';
  if (condition.startsWith('volume_')) return 'volume_above';
  return 'price_above';
}

function conditionDirection(condition: TradingAlertCondition): 'above' | 'below' {
  return condition.endsWith('_below') ? 'below' : 'above';
}

function updateCondition(
  family: TradingAlertCondition,
  direction: 'above' | 'below',
): TradingAlertCondition {
  const prefix = family.replace(/_above$/, '');
  return `${prefix}_${direction}` as TradingAlertCondition;
}

function expirationLabel(value: TradingAlertEditorState['expiration']): string {
  if (value === '1h') return '1 hour';
  if (value === '1d') return '1 day';
  if (value === '1w') return '1 week';
  return 'Never';
}

export function TradingAlertDialog({
  editor,
  symbol,
  latestPrice,
  status,
  onChange,
  onSubmit,
  onClose,
  onToggle,
  onArchive,
  indicatorChoices,
  targetChoices,
  targetNote,
}: {
  editor: TradingAlertEditorState;
  symbol: string;
  latestPrice: number;
  status: string;
  onChange: (patch: Partial<TradingAlertEditorState>) => void;
  onSubmit: () => void;
  onClose: () => void;
  onToggle?: () => void;
  onArchive?: () => void;
  /** The chart's indicators (TVP-1.3); without them the dialog offers the legacy indicator list. */
  indicatorChoices?: readonly AlertIndicatorChoice[];
  /** What a new alert can apply to (TVP-1.7): this symbol or a watchlist; and what a list alert runs on. */
  targetChoices?: ReadonlyArray<{ value: string; label: string }>;
  targetNote?: string | null;
}) {
  // On a chart with indicators, an indicator alert is on one of them (unless editing a legacy alert).
  const usesChartIndicators = Boolean(indicatorChoices?.length) && editor.mode === 'create' && editor.condition.startsWith('indicator_');
  const chartIndicators = indicatorChoices && usesChartIndicators
    ? resolveIndicatorSelection(indicatorChoices, editor.indicatorSelection, editor.chartIndicatorId)
    : undefined;
  const [conditionNote, setConditionNote] = useState<string | null>(null);
  const drafts = editor.conditionDrafts ?? [];
  const family = conditionFamily(editor.condition);
  const direction = conditionDirection(editor.condition);
  const isConditions = editor.condition === 'conditions';
  const isTrendline = editor.condition.startsWith('trendline_');
  const isIndicator = family === 'indicator_above';
  const isPercent = family === 'percent_change_above';
  // A new price alert can cross another line instead of a value (TVP-1.3), on a chart that offers lines.
  const priceLines = family === 'price_above' && editor.mode === 'create' && indicatorChoices !== undefined;

  return (
    <form
      className="trading-chart-alert-editor"
      role="dialog"
      aria-modal="false"
      aria-label={`${editor.mode === 'create' ? 'Create' : 'Edit'} alert on ${symbol}`}
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <header>
        <div className="trading-alert-dialog-title">
          <strong>{editor.mode === 'create' ? 'Create alert on' : 'Edit alert on'}</strong>
          <span className="trading-alert-dialog-symbol"><i aria-hidden="true">◈</i>{symbol}<span aria-hidden="true">⌄</span></span>
        </div>
        <button type="button" onClick={onClose} aria-label="Close alert editor">×</button>
      </header>

      <div className="trading-alert-dialog-body">
        <section className="trading-alert-condition-section" aria-label="Alert condition">
          <div className="trading-alert-section-heading"><strong>Condition</strong><span>Price, indicator, or volume</span></div>
          {targetChoices && targetChoices.length > 1 && editor.mode === 'create' ? (
            <div className="trading-alert-condition-row"><select aria-label="Alert applies to" value={editor.target ?? targetChoices[0].value} onChange={(event) => onChange({ target: event.target.value })}>{targetChoices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}</select></div>
          ) : null}
          {targetNote && editor.target?.startsWith('watchlist:') ? <small className="trading-alert-condition-note">{targetNote}</small> : null}
          {isConditions && editor.conditionsEditable !== false && drafts.length > 0 ? (
            <AlertConditionRows drafts={drafts} firstNumber={1} minimum={1} choices={indicatorChoices ?? []} onChange={(conditionDrafts) => { setConditionNote(null); onChange({ conditionDrafts, conditionError: undefined }); }} />
          ) : isConditions ? (
            <div className="trading-alert-value-row"><span>Conditions</span><strong>{editor.conditionsSummary || 'Conditions'}</strong></div>
          ) : (<>
          <div className="trading-alert-condition-row">
            <select
              aria-label="Alert condition"
              value={family}
              onChange={(event) => {
                const condition = updateCondition(event.target.value as TradingAlertCondition, direction);
                // A family that takes one condition drops the added ones.
                onChange({ condition, ...(acceptsExtraConditions(condition) && (!condition.startsWith('indicator_') || (Boolean(indicatorChoices?.length) && editor.mode === 'create')) ? {} : { conditionDrafts: [] }), conditionError: undefined });
              }}
            >
              {(isTrendline ? [{ value: 'trendline_crossing', label: 'Trendline' }] : priceConditionOptions).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
            {usesChartIndicators ? null : (
            <select
              aria-label="Alert crossing"
              value={isTrendline ? editor.condition : direction}
              onChange={(event) => onChange({ condition: isTrendline ? event.target.value as TradingAlertCondition : updateCondition(family, event.target.value as 'above' | 'below') })}
            >
              {isTrendline
                ? trendlineModeOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)
                : <><option value="above">Crossing above</option><option value="below">Crossing below</option></>}
            </select>
            )}
          </div>
          {isTrendline && editor.drawingLevels && editor.drawingLevels.length > 1 ? (
            <div className="trading-alert-value-row">
              <span>Level</span>
              <select
                aria-label="Alert drawing level"
                value={editor.drawingLevel}
                onChange={(event) => {
                  const level = editor.drawingLevels?.find((item) => item.key === event.target.value);
                  if (level) onChange({ drawingLevel: level.key, trendlinePoints: level.anchors.map((point) => ({ time: point.time, price: point.price })) });
                }}
              >
                {editor.drawingLevels.map((level) => <option key={level.key} value={level.key}>{level.label}</option>)}
              </select>
            </div>
          ) : isTrendline ? (
            <div className="trading-alert-value-row"><span>Line</span><strong>{editor.drawingLevels?.[0]?.label ?? 'Selected trendline'}</strong></div>
          ) : chartIndicators?.operator === 'appears' || chartIndicators?.target ? null : (
            <div className="trading-alert-value-row">
              <span>{priceLines ? 'Target' : 'Value'}</span>
              {priceLines ? (
                <AlertLineField label="Alert price" value="" line={editor.priceLine} choices={indicatorChoices ?? []} withInput={false} onValue={() => undefined} onLine={(priceLine) => onChange({ priceLine })} />
              ) : null}
              {editor.priceLine && priceLines ? null : (
                <input
                  aria-label="Alert value"
                  autoFocus
                  inputMode="decimal"
                  value={editor.threshold}
                  placeholder={Number.isFinite(latestPrice) ? String(latestPrice) : 'Value'}
                  onChange={(event) => onChange({ threshold: event.target.value })}
                  onBlur={(event) => onChange({ threshold: formatAlertThreshold(event.target.value) })}
                />
              )}
            </div>
          )}
          {isPercent ? (
            <label className="trading-alert-inline-field">Lookback bars<input inputMode="numeric" value={editor.lookback} onChange={(event) => onChange({ lookback: event.target.value })} /></label>
          ) : null}
          {usesChartIndicators && !indicatorChoices?.some((choice) => !choice.unavailable) ? (
            <small className="trading-alert-condition-note" role="alert">None of this chart&apos;s indicators can be alerted on by the server yet.</small>
          ) : usesChartIndicators ? (
            <AlertIndicatorPicker choices={indicatorChoices ?? []} selection={chartIndicators} onChange={(indicatorSelection) => onChange({ indicatorSelection })} />
          ) : isIndicator ? (
            <div className="trading-alert-condition-row">
              <select aria-label="Alert indicator" value={editor.indicator} onChange={(event) => onChange({ indicator: event.target.value as TradingAlertIndicatorId })}>
                {['sma', 'ema', 'rsi', 'macd', 'bollinger', 'atr', 'vwap', 'stochastic-rsi'].map((item) => <option key={item} value={item}>{item.toUpperCase()}</option>)}
              </select>
              <input aria-label="Alert indicator period" inputMode="numeric" value={editor.period} onChange={(event) => onChange({ period: event.target.value })} />
            </div>
          ) : null}
          <AlertConditionRows drafts={drafts} firstNumber={2} minimum={0} choices={indicatorChoices ?? []} onChange={(conditionDrafts) => { setConditionNote(null); onChange({ conditionDrafts, conditionError: undefined }); }} />
          </>)}
          <button
            type="button"
            className="trading-alert-add-condition"
            onClick={() => {
              const blocked = addConditionBlock(editor, usesChartIndicators);
              setConditionNote(blocked);
              if (!blocked) onChange({ conditionDrafts: [...drafts, newConditionDraft(Number.isFinite(latestPrice) ? String(latestPrice) : '')] });
            }}
          >
            ＋ Add condition
          </button>
          {conditionNote ? <small className="trading-alert-condition-note" role="status">{conditionNote}</small> : null}
          {drafts.length > 0 && !isConditions ? <small className="trading-alert-condition-note">All conditions must hold together (AND); one frequency and message. The alert is then listed by its conditions, without a line on the chart.</small> : null}
          {editor.conditionError ? <small className="trading-alert-condition-note" role="alert">{editor.conditionError}</small> : null}
        </section>

        <dl className="trading-alert-dialog-settings">
          <div>
            <dt>Trigger</dt>
            <dd><select aria-label="Alert trigger" value={editor.triggerPolicy} onChange={(event) => onChange({ triggerPolicy: event.target.value as TradingAlertTriggerPolicy })}>{triggerOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></dd>
          </div>
          <div>
            <dt>Expiration</dt>
            <dd>
              <select aria-label="Alert expiration" value={editor.expiration} onChange={(event) => onChange({ expiration: event.target.value as TradingAlertEditorState['expiration'] })}>
                {(['never', '1h', '1d', '1w'] as const).map((item) => <option key={item} value={item}>{expirationLabel(item)}</option>)}
              </select>
              {editor.expiration !== 'never' ? <input aria-label="Alert expiration date" type="datetime-local" value={editor.expiresAt} onChange={(event) => onChange({ expiresAt: event.target.value })} /> : null}
            </dd>
          </div>
          <div>
            <dt>Name</dt>
            <dd><input aria-label="Alert name" value={editor.name ?? ''} placeholder="Optional" maxLength={120} onChange={(event) => onChange({ name: event.target.value })} /></dd>
          </div>
          <div>
            <dt>Message</dt>
            <dd>
              <textarea aria-label="Alert message" rows={2} value={editor.message} placeholder={isTrendline ? `${symbol} crossing trendline` : `${symbol} crossing ${editor.threshold || 'value'}`} maxLength={500} onChange={(event) => onChange({ message: event.target.value })} />
              <small className="trading-alert-placeholders" title="Filled in when the alert triggers; unknown ones stay as written">Placeholders: {MESSAGE_PLACEHOLDERS.join(' ')}</small>
            </dd>
          </div>
          <div>
            <dt>Notifications</dt>
            <dd className="trading-alert-notifications">
              <AlertDeliveryFields editor={editor} onChange={onChange} />
            </dd>
          </div>
        </dl>
      </div>

      <footer className="trading-chart-alert-editor-actions">
        <small className="trading-chart-alert-editor-status">Server state: {status}</small>
        <button type="button" onClick={onClose}>Cancel</button>
        {onToggle ? <button type="button" onClick={onToggle}>{editor.mode === 'edit' ? 'Disable / enable' : 'Test'}</button> : null}
        {onArchive ? <button type="button" className="danger" onClick={onArchive}>Delete</button> : null}
        <button type="submit" disabled={status === 'saving'}>{editor.mode === 'create' ? 'Create alert' : 'Save changes'}</button>
      </footer>
    </form>
  );
}
