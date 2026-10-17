/**
 * Multi-condition alerts (TVP-1.6): the dialog edits conditions as drafts and sends them as the alert's `conditions`
 * (up to five, combined with AND; the server model is `alert_conditions.py`).
 *
 * A legacy alert (price, volume, percent change) with extra conditions becomes a conditions alert whose first
 * condition has the meaning the legacy one always had: crossing up through the value for "above", down for "below"
 * (`legacy_conditions` on the server). Drawing alerts stay single-condition: they follow their drawing through their
 * own line (TVP-1.4).
 */
import type { components } from './api/generated';
import type { AlertIndicatorChoice } from './alertIndicatorSources';
import type { TradingAlert, TradingAlertCreateInput, TradingAlertUpdateInput } from './tradingTypes';

export const MAX_ALERT_CONDITIONS = 5;

type ConditionInput = components['schemas']['AlertConditionSpec-Input'];
type ConditionOutput = components['schemas']['AlertConditionSpec-Output'];
export type AlertOperator = ConditionInput['operator'];
type IndicatorInputs = components['schemas']['IndicatorSource-Input']['inputs'];

export type ConditionSourceKind = 'close' | 'open' | 'high' | 'low' | 'hl2' | 'hlc3' | 'ohlc4' | 'volume' | 'change' | 'indicator';
type PriceField = Exclude<ConditionSourceKind, 'change' | 'indicator'>;

/**
 * Another line as a condition's target or channel bound, instead of a number (TVP-1.3): a price field, or an indicator
 * line (its id, inputs and output, as the chart computes it). The server evaluates both sides on the same bars.
 */
export type DraftLine =
  | { kind: 'price'; field: PriceField }
  | { kind: 'indicator'; indicatorId: string; indicatorInputs?: IndicatorInputs; output: string };

export type ConditionDraft = {
  source: ConditionSourceKind;
  /** Change %: bars back. */
  lookback: string;
  /** Indicator: the indicator, its inputs and line. */
  indicatorId?: string;
  indicatorInputs?: IndicatorInputs;
  output?: string;
  operator: AlertOperator;
  value: string;
  upper: string;
  lower: string;
  /** A line instead of `value`, `upper` or `lower`. */
  targetLine?: DraftLine;
  upperLine?: DraftLine;
  lowerLine?: DraftLine;
  amount: string;
  bars: string;
};

export const SOURCE_OPTIONS: ReadonlyArray<{ value: ConditionSourceKind; label: string }> = [
  { value: 'close', label: 'Close' },
  { value: 'open', label: 'Open' },
  { value: 'high', label: 'High' },
  { value: 'low', label: 'Low' },
  { value: 'hl2', label: 'HL2' },
  { value: 'hlc3', label: 'HLC3' },
  { value: 'ohlc4', label: 'OHLC4' },
  { value: 'volume', label: 'Volume' },
  { value: 'change', label: 'Change %' },
  { value: 'indicator', label: 'Indicator' },
];

export const OPERATOR_OPTIONS: ReadonlyArray<{ value: AlertOperator; label: string; takes: 'value' | 'channel' | 'moving' }> = [
  { value: 'crossing', label: 'Crossing', takes: 'value' },
  { value: 'crossing_up', label: 'Crossing up', takes: 'value' },
  { value: 'crossing_down', label: 'Crossing down', takes: 'value' },
  { value: 'greater_than', label: 'Greater than', takes: 'value' },
  { value: 'less_than', label: 'Less than', takes: 'value' },
  { value: 'entering_channel', label: 'Entering channel', takes: 'channel' },
  { value: 'exiting_channel', label: 'Exiting channel', takes: 'channel' },
  { value: 'inside_channel', label: 'Inside channel', takes: 'channel' },
  { value: 'outside_channel', label: 'Outside channel', takes: 'channel' },
  { value: 'moving_up', label: 'Moving up', takes: 'moving' },
  { value: 'moving_down', label: 'Moving down', takes: 'moving' },
  { value: 'moving_up_percent', label: 'Moving up %', takes: 'moving' },
  { value: 'moving_down_percent', label: 'Moving down %', takes: 'moving' },
];

export function operatorTakes(operator: AlertOperator): 'value' | 'channel' | 'moving' {
  return OPERATOR_OPTIONS.find((option) => option.value === operator)?.takes ?? 'value';
}

export function newConditionDraft(value = ''): ConditionDraft {
  return { source: 'close', lookback: '1', operator: 'crossing', value, upper: '', lower: '', amount: '', bars: '1' };
}

const isNumber = (text: string) => text.trim() !== '' && Number.isFinite(Number(text));
const PRICE_FIELDS = new Set<ConditionSourceKind>(['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4', 'volume']);
export const PRICE_LINE_OPTIONS = SOURCE_OPTIONS.filter((option) => PRICE_FIELDS.has(option.value)) as ReadonlyArray<{ value: PriceField; label: string }>;

/** The condition source a line reads. */
export function lineSource(line: DraftLine): ConditionInput['source'] {
  return line.kind === 'price'
    ? { kind: 'price', field: line.field }
    : { kind: 'indicator', indicator_id: line.indicatorId, inputs: line.indicatorInputs ?? {}, output: line.output };
}

/** The line a stored source is, when the dialog can offer it as a target (a price field or an indicator line). */
function sourceLine(source: ConditionOutput['source'] | ConditionInput['source']): DraftLine | null {
  if (source.kind === 'price') return { kind: 'price', field: (source.field ?? 'close') as PriceField };
  if (source.kind === 'indicator') return { kind: 'indicator', indicatorId: source.indicator_id, indicatorInputs: source.inputs as IndicatorInputs, output: source.output };
  return null;
}

/** A chart indicator's line as a target. */
export function choiceLine(choice: AlertIndicatorChoice, output: string): DraftLine {
  return { kind: 'indicator', indicatorId: choice.key, indicatorInputs: choice.inputs as IndicatorInputs, output };
}

/** Whether a chart indicator's line is the stored line (same indicator, inputs and output). */
export function choiceMatchesLine(choice: AlertIndicatorChoice, line: DraftLine | undefined, output: string): boolean {
  if (!line || line.kind !== 'indicator' || line.output !== output) return false;
  return choiceMatchesDraft(choice, { ...newConditionDraft(), indicatorId: line.indicatorId, indicatorInputs: line.indicatorInputs, output: line.output });
}

type Bound = components['schemas']['ChannelTarget-Input']['upper'];
const bound = (value: string, line: DraftLine | undefined): Bound => (line ? { kind: 'source', source: lineSource(line) } : { kind: 'value', value: value.trim() });

/** The condition a draft describes, or why it can't be sent. */
export function draftCondition(draft: ConditionDraft): ConditionInput | string {
  let source: ConditionInput['source'];
  if (PRICE_FIELDS.has(draft.source)) source = { kind: 'price', field: draft.source as 'close' };
  else if (draft.source === 'change') {
    const lookback = Number(draft.lookback);
    if (!Number.isInteger(lookback) || lookback < 1 || lookback > 500) return 'Change % needs 1 to 500 bars back.';
    source = { kind: 'change_percent', lookback_bars: lookback };
  } else {
    if (!draft.indicatorId || !draft.output) return 'Choose an indicator and its line.';
    source = { kind: 'indicator', indicator_id: draft.indicatorId, inputs: draft.indicatorInputs ?? {}, output: draft.output };
  }
  const takes = operatorTakes(draft.operator);
  if (takes === 'moving') {
    const bars = Number(draft.bars);
    if (!isNumber(draft.amount) || Number(draft.amount) <= 0) return 'Moving conditions need an amount above zero.';
    if (!Number.isInteger(bars) || bars < 1 || bars > 500) return 'Moving conditions need 1 to 500 bars.';
    return { source, operator: draft.operator, amount: draft.amount.trim(), bars };
  }
  if (takes === 'channel') {
    if ((!draft.upperLine && !isNumber(draft.upper)) || (!draft.lowerLine && !isNumber(draft.lower))) return 'A channel needs an upper and a lower value or line.';
    if (!draft.upperLine && !draft.lowerLine && Number(draft.upper) <= Number(draft.lower)) return 'The upper value must be above the lower value.';
    return { source, operator: draft.operator, target: { kind: 'channel', upper: bound(draft.upper, draft.upperLine), lower: bound(draft.lower, draft.lowerLine) } };
  }
  if (!draft.targetLine && !isNumber(draft.value)) return 'Each condition needs a value or a line.';
  return { source, operator: draft.operator, target: bound(draft.value, draft.targetLine) };
}

type StoredBound = { kind: 'value'; value: string | number } | { kind: 'source'; source: ConditionOutput['source'] | ConditionInput['source'] };

/** A stored value or line bound as the draft's text and line; null for a line the dialog can't offer (a trendline). */
function boundDraft(stored: StoredBound): { text: string; line?: DraftLine } | null {
  if (stored.kind === 'value') return { text: String(stored.value) };
  const line = sourceLine(stored.source);
  return line ? { text: '', line } : null;
}

/** The draft of a stored condition, or null when the dialog can't edit it (a trendline or script among its parts). */
export function conditionDraft(condition: ConditionOutput | ConditionInput): ConditionDraft | null {
  const draft = newConditionDraft();
  const source = condition.source;
  if (source.kind === 'price') draft.source = (source.field ?? 'close') as ConditionSourceKind;
  else if (source.kind === 'change_percent') Object.assign(draft, { source: 'change', lookback: String(source.lookback_bars ?? 1) });
  else if (source.kind === 'indicator') Object.assign(draft, { source: 'indicator', indicatorId: source.indicator_id, indicatorInputs: source.inputs, output: source.output });
  else return null;
  draft.operator = condition.operator;
  const takes = operatorTakes(condition.operator);
  if (takes === 'moving') return { ...draft, amount: String(condition.amount ?? ''), bars: String(condition.bars ?? 1) };
  const target = condition.target;
  if (takes === 'channel' && target?.kind === 'channel') {
    const upper = boundDraft(target.upper as StoredBound);
    const lower = boundDraft(target.lower as StoredBound);
    return upper && lower ? { ...draft, upper: upper.text, upperLine: upper.line, lower: lower.text, lowerLine: lower.line } : null;
  }
  if (takes === 'value' && (target?.kind === 'value' || target?.kind === 'source')) {
    const value = boundDraft(target as StoredBound);
    return value ? { ...draft, value: value.text, targetLine: value.line } : null;
  }
  return null;
}

/** Whether a chart indicator is the one a draft reads: the same indicator and inputs, drawing the draft's line. */
export function choiceMatchesDraft(choice: AlertIndicatorChoice, draft: ConditionDraft): boolean {
  if (choice.key !== draft.indicatorId || !choice.outputs.some((output) => output.key === draft.output)) return false;
  const inputs = (value: unknown) => JSON.stringify(Object.entries((value ?? {}) as Record<string, unknown>).filter(([, item]) => item !== null && item !== undefined).sort(([a], [b]) => a.localeCompare(b)));
  return inputs(choice.inputs) === inputs(draft.indicatorInputs);
}

/** A chart indicator for a draft: its id, inputs and first line. */
export function indicatorDraftPatch(choice: AlertIndicatorChoice, output?: string): Partial<ConditionDraft> {
  return { indicatorId: choice.key, indicatorInputs: choice.inputs as IndicatorInputs, output: output ?? choice.outputs[0]?.key };
}

/** The editor fields for an alert's conditions: drafts, and whether every condition can be edited. */
export function conditionEditorFields(alert: Pick<TradingAlert, 'condition_type' | 'conditions'>): { conditionDrafts: ConditionDraft[]; conditionsEditable: boolean } {
  if (alert.condition_type !== 'conditions') return { conditionDrafts: [], conditionsEditable: true };
  const drafts = (alert.conditions ?? []).map((condition) => conditionDraft(condition as ConditionOutput));
  return drafts.every((draft) => draft !== null) ? { conditionDrafts: drafts as ConditionDraft[], conditionsEditable: true } : { conditionDrafts: [], conditionsEditable: false };
}

/** Whether an alert of this kind can take more conditions in the dialog. */
export function acceptsExtraConditions(condition: string): boolean {
  return condition === 'conditions' || /^(price|volume|percent_change)_(above|below)$/.test(condition) || condition.startsWith('indicator_');
}

type EditorConditions = { condition: string; conditionDrafts?: ConditionDraft[]; conditionsEditable?: boolean };
type AlertInput = Pick<TradingAlertCreateInput | TradingAlertUpdateInput, 'condition_type' | 'threshold' | 'parameters' | 'conditions'>;

/** The legacy single condition's meaning, as the server reads it (`legacy_conditions`). */
function legacyCondition(input: AlertInput): ConditionInput | null {
  const type = input.condition_type ?? '';
  const operator: AlertOperator = type.endsWith('_above') ? 'crossing_up' : 'crossing_down';
  const target = { kind: 'value' as const, value: String(input.threshold) };
  if (type.startsWith('price_')) return { source: { kind: 'price', field: 'close' }, operator, target };
  if (type.startsWith('volume_')) return { source: { kind: 'price', field: 'volume' }, operator, target };
  if (type.startsWith('percent_change_')) return { source: { kind: 'change_percent', lookback_bars: Number(input.parameters?.lookback_bars) || 1 }, operator, target };
  return null;
}

/** Whether a price alert compares against a line rather than its value (so it has no value of its own). */
export function isPriceLineAlert(condition: string, line: DraftLine | undefined): boolean {
  return Boolean(line) && (condition === 'price_above' || condition === 'price_below');
}

/**
 * A new price alert against a line (TVP-1.3) becomes a conditions alert: the close crossing up through the line for
 * "Crossing above", down for "Crossing below", as a price alert crosses its value. Without a line nothing changes.
 */
export function withPriceLineCondition(input: AlertInput, condition: string, line: DraftLine | undefined): void {
  if (!line || !isPriceLineAlert(condition, line)) return;
  input.condition_type = 'conditions';
  input.threshold = '0';
  input.conditions = [{
    source: { kind: 'price', field: 'close' },
    operator: condition === 'price_above' ? 'crossing_up' : 'crossing_down',
    target: { kind: 'source', source: lineSource(line) },
  }];
  input.parameters = { ...input.parameters, indicator_id: null };
}

/**
 * Applies the dialog's conditions to a create or update request: a conditions alert gets its drafts; a legacy alert
 * with extra conditions becomes a conditions alert (its own condition first). Returns an error message, or null.
 */
export function applyConditionDrafts(input: AlertInput, editor: EditorConditions, report?: (problem: string) => void): string | null {
  const problem = conditionDraftsProblem(input, editor);
  if (problem) report?.(problem);
  return problem;
}

function conditionDraftsProblem(input: AlertInput, editor: EditorConditions): string | null {
  const drafts = editor.conditionDrafts ?? [];
  const isConditions = editor.condition === 'conditions';
  if (isConditions && editor.conditionsEditable === false) return null;
  if (!isConditions && drafts.length === 0) return null;
  const extra: ConditionInput[] = [];
  for (const draft of drafts) {
    const condition = draftCondition(draft);
    if (typeof condition === 'string') return condition;
    extra.push(condition);
  }
  let conditions = extra;
  if (!isConditions) {
    // A chart indicator alert is already a conditions request with its one condition (TVP-1.3).
    const first = input.condition_type === 'conditions' ? input.conditions?.[0] : legacyCondition(input);
    if (!first) return 'This alert takes one condition: add the others in a separate alert.';
    conditions = [first, ...extra];
  }
  if (conditions.length === 0) return 'An alert needs at least one condition.';
  if (conditions.length > MAX_ALERT_CONDITIONS) return `An alert takes at most ${MAX_ALERT_CONDITIONS} conditions.`;
  input.condition_type = 'conditions';
  input.threshold = '0';
  input.conditions = conditions;
  input.parameters = { ...input.parameters, indicator_id: null };
  return null;
}
