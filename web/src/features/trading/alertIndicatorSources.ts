// Alerts on any chart indicator and output (TVP-1.3). The alert dialog
// offers the chart's indicators, each with the output lines the chart draws;
// an indicator the server can't evaluate is offered greyed out with the
// reason. The alert is created as a conditions alert with the indicator's
// inputs, params and compare symbol, so the server evaluates the same
// indicator and output on its own bars, in the instrument's session hours.
import type { CoreIndicatorInstance, IndicatorOutput } from './indicators/coreIndicators';
import { orderBySources, resolveSourceOutput } from './indicators/indicatorSources';
import { isExternalIndicatorId } from './indicators/externalIndicatorData';
import { tradingViewBuiltInUsesCompareSeries } from './indicators/tradingViewBuiltIns';
import { indicatorContextLabel } from './tradingChartPanelModel';
import { isScriptIndicatorId, scriptAlertOutput, scriptIdOf, scriptIndicatorName, scriptInputValues, scriptRunStatus } from './scripts/scriptIndicators';
import type { components } from './api/generated';
import type { TradingAlertCreateInput } from './tradingTypes';
import { lineSource, type DraftLine } from './alertConditionDrafts';

type ConditionInput = NonNullable<TradingAlertCreateInput['conditions']>[number];

/** `appears`: a signal output (markers: candlestick patterns, fractals) has a value on this bar; stored as greater than 0. */
export type AlertIndicatorOperator = 'crossing' | 'crossing_up' | 'crossing_down' | 'greater_than' | 'less_than' | 'appears';

export const ALERT_INDICATOR_OPERATORS: Array<{ value: AlertIndicatorOperator; label: string }> = [
  { value: 'crossing', label: 'Crossing' },
  { value: 'crossing_up', label: 'Crossing Up' },
  { value: 'crossing_down', label: 'Crossing Down' },
  { value: 'greater_than', label: 'Greater Than' },
  { value: 'less_than', label: 'Less Than' },
];
/**
 * "Appears" is stored as the output greater than this: any value at all, even a negative price (a signal is valued at
 * its bar's low or high), and a target no line alert uses, so the chart draws no line for it.
 */
export const APPEARS_VALUE = '-1000000000000000000';
/** Signals marked bars after the fact (a fractal is confirmed `radius` bars later): never on the bar alerts evaluate. */
const LAGGED_SIGNAL_INDICATORS = new Set(['tv-williams-fractal']);
/** The comparison a signal output offers: it has a value only on the bars where the signal appears. */
export const ALERT_SIGNAL_OPERATORS: Array<{ value: AlertIndicatorOperator; label: string }> = [{ value: 'appears', label: 'Appears' }];

export type AlertIndicatorChoice = {
  /** The indicator id; one instance per id on a chart. */
  key: string;
  label: string;
  /** `signal`: drawn as markers, valued (at the bar's price) only on the bars where it appears. */
  outputs: Array<{ key: string; title: string; signal?: true }>;
  inputs: components['schemas']['IndicatorSourceInputs-Input'];
  /** A script indicator (TVP-11.4): the saved revision an alert on it runs, with this chart's inputs. */
  script?: { scriptId: string; revision: number; inputs: Record<string, string | number | boolean> };
  /** Why the server can't alert on it, when it can't. */
  unavailable?: string;
};

/** A script indicator as an alert source: its plots, alertcondition() and alert() calls, from the chart's last run. */
function scriptChoice(instance: CoreIndicatorInstance, outputs: readonly IndicatorOutput[]): AlertIndicatorChoice {
  const id = String(instance.id);
  const scriptId = scriptIdOf(id) ?? '';
  const status = scriptRunStatus(scriptId);
  const plots = outputs.filter((output) => output.key.startsWith(`${id}:`) && /^p\d+$/.test(output.key.slice(id.length + 1)))
    .map((output) => ({ key: output.key, title: output.title, ...(output.render === 'markers' ? { signal: true as const } : {}) }));
  const lines = [...plots, ...(status?.signals ?? []).map((signal) => ({ ...signal, signal: true as const }))];
  const unavailable = !status?.revision ? 'Waiting for the script to run on the chart' : lines.length === 0 ? 'It has no plot, alertcondition() or alert() to alert on' : undefined;
  const inputs = Object.fromEntries(Object.entries(scriptInputValues(instance)).filter((entry): entry is [string, string | number | boolean] => ['string', 'number', 'boolean'].includes(typeof entry[1])));
  return {
    key: id, label: scriptIndicatorName(instance) ?? id, outputs: lines, inputs: inputsOf(instance),
    script: { scriptId, revision: status?.revision ?? Number(instance.params?.revision ?? 1), inputs }, ...(unavailable ? { unavailable } : {}),
  };
}

/** What the dialog holds for a chart-indicator condition; `target` compares against another line instead of the value. */
export type AlertIndicatorSelection = { key: string; output: string; operator: AlertIndicatorOperator; target?: DraftLine };

/**
 * The alert inputs of an instance; with its source's (TVP-6.5) when the chart computes it on another indicator. The
 * source is resolved as the chart resolves it (`orderBySources`), and its output key is the one the chart draws now.
 */
function inputsOf(instance: CoreIndicatorInstance, instances: readonly CoreIndicatorInstance[] = [], outputs: readonly IndicatorOutput[] = []): AlertIndicatorChoice['inputs'] {
  const resolved = instance.source ? orderBySources(instances.filter((item) => item.enabled)).find((item) => item.id === instance.id)?.source : null;
  const sourceInstance = resolved ? instances.find((item) => item.id === resolved.indicatorId) : undefined;
  const output = resolved ? resolveSourceOutput(resolved, outputs)?.key ?? resolved.output : null;
  return {
    source: resolved && sourceInstance && output
      ? { indicator_id: String(sourceInstance.id), inputs: inputsOf(sourceInstance), output }
      : null,
    period: instance.period,
    fast_period: instance.fastPeriod ?? null,
    slow_period: instance.slowPeriod ?? null,
    signal_period: instance.signalPeriod ?? null,
    standard_deviations: instance.standardDeviations ?? null,
    anchor_time: instance.anchorTime || null,
    anchor_bars_ago: null,
    // The indicator's own inputs and compare symbol; the server takes the session hours from the instrument (TVP-1.3).
    params: Object.fromEntries(Object.entries(instance.params ?? {}).filter((entry): entry is [string, number | string] => (
      typeof entry[1] === 'string' || (typeof entry[1] === 'number' && Number.isFinite(entry[1]))))),
    compare_symbol: instance.compareSymbol || null,
  };
}

function isSignalOutput(output: IndicatorOutput): boolean {
  return output.render === 'markers' || output.kind === 'bar-colors' || output.kind === 'background';
}

/** The chart's enabled indicators as alert sources; `serverIds` null while the server's list loads. */
export function alertIndicatorChoices(
  instances: readonly CoreIndicatorInstance[],
  outputs: readonly IndicatorOutput[],
  serverIds: ReadonlySet<string> | null,
): AlertIndicatorChoice[] {
  return instances.filter((instance) => instance.enabled).map((instance) => {
    if (isScriptIndicatorId(String(instance.id))) return scriptChoice(instance, outputs);
    const source = instance.source ? instances.find((item) => item.id === instance.source!.indicatorId) : undefined;
    // Markers, and what the chart paints (bar colours, background), are signals: they hold only on the bars they mark.
    const lines = outputs.filter((output) => output.key.split(':', 1)[0] === instance.id)
      .map((output) => ({ key: output.key, title: output.title, ...(isSignalOutput(output) ? { signal: true as const } : {}) }));
    const unavailable = serverIds === null
      ? 'Checking which indicators server alerts support…'
      : !serverIds.has(instance.id)
        ? 'Server alerts are not available for this indicator yet'
        // An indicator on another indicator needs the server to compute its source the same way; a data series
        // (TVP-0.2) as the source is computed in the browser only.
        : instance.source && (!source || !serverIds.has(String(source.id)) || isExternalIndicatorId(String(source.id)))
          ? 'Its source indicator is not evaluated by server alerts yet'
        : tradingViewBuiltInUsesCompareSeries(instance.id) && !instance.compareSymbol
          ? 'Choose its compare symbol on the chart first'
        : lines.length === 0
          ? 'It draws no line to alert on'
          : LAGGED_SIGNAL_INDICATORS.has(instance.id)
            ? 'Its marks are confirmed bars later, so server alerts never see them on the latest bar'
            : undefined;
    return { key: instance.id, label: indicatorContextLabel(instance), outputs: lines, inputs: inputsOf(instance, instances, outputs), ...(unavailable ? { unavailable } : {}) };
  });
}

/**
 * The condition an indicator selection describes: the output against a value or another line (TVP-1.3); "appears" is
 * the output above APPEARS_VALUE.
 */
export function indicatorConditionSpec(choice: AlertIndicatorChoice, selection: AlertIndicatorSelection, value: string): ConditionInput {
  const appears = selection.operator === 'appears';
  const output = choice.script ? scriptAlertOutput(selection.output) : null;
  return {
    source: choice.script && output
      ? { kind: 'script', script_id: choice.script.scriptId, revision: choice.script.revision, inputs: choice.script.inputs, output }
      : { kind: 'indicator', indicator_id: choice.key, inputs: choice.inputs, output: selection.output },
    operator: selection.operator === 'appears' ? 'greater_than' : selection.operator,
    target: appears ? { kind: 'value', value: APPEARS_VALUE }
      : selection.target ? { kind: 'source', source: lineSource(selection.target) }
      : { kind: 'value', value },
  };
}

/** The operator a selection keeps on another output: "appears" for a signal, else the line's comparison (crossing by default). */
export function operatorForOutput(choice: AlertIndicatorChoice | undefined, output: string, operator: AlertIndicatorOperator | undefined): AlertIndicatorOperator {
  const signal = choice?.outputs.find((item) => item.key === output)?.signal === true;
  if (signal) return 'appears';
  return operator && operator !== 'appears' ? operator : 'crossing';
}

/**
 * Turns a create request into a conditions alert on the selected chart indicator, when the dialog has one; returns
 * whether it did. The legacy indicator fields are cleared: the condition says everything.
 */
export function withChartIndicatorCondition(
  input: TradingAlertCreateInput,
  choices: readonly AlertIndicatorChoice[] | undefined,
  selection: AlertIndicatorSelection | undefined,
  value: string,
): boolean {
  const choice = selection ? choices?.find((item) => item.key === selection.key && !item.unavailable) : undefined;
  if (!choice || !selection || !choice.outputs.some((output) => output.key === selection.output)) return false;
  input.condition_type = 'conditions';
  input.threshold = '0';
  input.conditions = [indicatorConditionSpec(choice, selection, value)];
  input.parameters = { ...input.parameters, indicator_id: null };
  return true;
}

/** The selection a dialog starts with: the indicator the alert was placed on, else the first the server can alert on. */
export function defaultIndicatorSelection(choices: readonly AlertIndicatorChoice[], preferKey?: string): AlertIndicatorSelection | undefined {
  // Placed on a pane whose indicator can't be alerted on: no stand-in (its value is on another scale); the user picks.
  if (preferKey && choices.some((item) => item.key === preferKey && item.unavailable)) return undefined;
  const choice = choices.find((item) => item.key === preferKey && !item.unavailable) ?? choices.find((item) => !item.unavailable);
  return choice ? { key: choice.key, output: choice.outputs[0].key, operator: operatorForOutput(choice, choice.outputs[0].key, undefined) } : undefined;
}

/**
 * A selection that still fits the chart: an indicator that went away (or became unavailable) falls back to the
 * default, a line that went away to the indicator's first line. Undefined when nothing can be alerted on.
 */
export function resolveIndicatorSelection(
  choices: readonly AlertIndicatorChoice[],
  selection: AlertIndicatorSelection | undefined,
  preferKey?: string,
): AlertIndicatorSelection | undefined {
  const choice = selection ? choices.find((item) => item.key === selection.key && !item.unavailable) : undefined;
  if (!choice || !selection) return defaultIndicatorSelection(choices, preferKey);
  const output = choice.outputs.some((item) => item.key === selection.output) ? selection.output : choice.outputs[0].key;
  return { ...selection, output, operator: operatorForOutput(choice, output, selection.operator) };
}

type ChartAlert = { condition_type: string; threshold: string | number; parameters: { indicator_id?: string | null }; conditions?: readonly unknown[] };
type IndicatorValueCondition = { source: { kind: string; indicator_id?: string }; operator?: string; target?: { kind: string; value?: string | number } };

function singleIndicatorCondition(alert: ChartAlert): IndicatorValueCondition | null {
  if (alert.condition_type !== 'conditions' || alert.conditions?.length !== 1) return null;
  const condition = alert.conditions[0] as IndicatorValueCondition;
  // An "appears" alert has no line to draw or drag.
  if (isAppearsCondition(condition)) return null;
  return condition.source.kind === 'indicator' && condition.target?.kind === 'value' ? condition : null;
}

/** Whether a condition is a signal output's "appears" (greater than APPEARS_VALUE). */
export function isAppearsCondition(condition: { source: { kind: string }; operator?: string; target?: { kind: string; value?: string | number } | null }): boolean {
  return (condition.source.kind === 'indicator' || condition.source.kind === 'script') && condition.operator === 'greater_than' && condition.target?.kind === 'value'
    && Number(condition.target.value) <= Number(APPEARS_VALUE) / 10;
}

/** The indicator a single indicator condition reads instead of the close (TVP-6.5), if any. */
export function chartAlertSourceIndicatorId(alert: ChartAlert): string | null {
  const condition = singleIndicatorCondition(alert) as (IndicatorValueCondition & { source: { inputs?: { source?: { indicator_id?: string } | null } } }) | null;
  return condition?.source.inputs?.source?.indicator_id ?? null;
}

/** The indicator an alert is drawn on: a legacy indicator alert's, or a single "indicator line vs value" condition's. */
export function chartAlertIndicatorId(alert: ChartAlert): string | null {
  if (alert.condition_type.startsWith('indicator_')) return alert.parameters.indicator_id ?? null;
  return singleIndicatorCondition(alert)?.source.indicator_id ?? null;
}

/** The value an alert is drawn at: its threshold, or its single indicator condition's value. */
export function chartAlertThreshold(alert: ChartAlert): number {
  const condition = singleIndicatorCondition(alert);
  return Number(condition ? condition.target?.value : alert.threshold);
}

/** An alert's conditions with the single indicator condition moved to `value` (a dragged line). */
export function conditionsAtValue<T extends ChartAlert>(alert: T, value: string): T['conditions'] {
  if (!singleIndicatorCondition(alert)) return alert.conditions;
  const [condition] = alert.conditions as IndicatorValueCondition[];
  return [{ ...condition, target: { kind: 'value', value } }] as T['conditions'];
}
