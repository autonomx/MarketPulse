import { useMemo } from 'react';
import { indicatorLines } from './indicatorLineChoices';
import { isExternalIndicatorId } from './indicators/externalIndicatorData';
import { newScreenerRule, SCREENER_METRICS, SCREENER_OPERATORS, type ScreenerMetric, type ScreenerRuleInput } from './screenerRules';

type IndicatorSource = { kind: 'indicator'; indicator_id: string; inputs: { period: number }; output: string };

function IndicatorFields({ rule, indicatorIds, onChange }: { rule: ScreenerRuleInput; indicatorIds: readonly string[]; onChange: (rule: ScreenerRuleInput) => void }) {
  const source = (rule.source ?? null) as IndicatorSource | null;
  const indicatorId = source?.indicator_id ?? indicatorIds[0] ?? '';
  const period = source?.inputs.period ?? rule.period ?? 14;
  const lines = useMemo(() => (indicatorId ? indicatorLines(indicatorId, period) : []), [indicatorId, period]);
  const set = (next: { indicator_id?: string; period?: number; output?: string }) => {
    const id = next.indicator_id ?? indicatorId;
    const length = next.period ?? period;
    const output = next.output ?? (next.indicator_id || next.period ? indicatorLines(id, length)[0]?.key : source?.output) ?? '';
    onChange({ ...rule, period: length, source: { kind: 'indicator', indicator_id: id, inputs: { period: length }, output } as never });
  };
  return (
    <>
      <select aria-label={`Indicator of ${rule.rule_id}`} value={indicatorId} onChange={(event) => set({ indicator_id: event.target.value })}>
        {indicatorIds.map((id) => <option key={id} value={id}>{id}</option>)}
      </select>
      {isExternalIndicatorId(indicatorId) ? null : <input aria-label={`Indicator period of ${rule.rule_id}`} inputMode="numeric" value={period} onChange={(event) => set({ period: Number(event.target.value) || 1 })} />}
      <select aria-label={`Indicator line of ${rule.rule_id}`} value={source?.output ?? ''} onChange={(event) => set({ output: event.target.value })}>
        {source?.output ? null : <option value="" disabled>Choose a line</option>}
        {lines.map((line) => <option key={line.key} value={line.key}>{line.title} ({line.key})</option>)}
      </select>
    </>
  );
}

/** The screen's filters and columns (TVP-9.1): metric, comparison and value; columns are shown only. */
export function ScreenerRuleEditor({
  rules, indicatorIds: serverIds, onChange,
}: {
  rules: readonly ScreenerRuleInput[];
  indicatorIds: readonly string[];
  onChange: (rules: ScreenerRuleInput[]) => void;
}) {
  // The server runs each symbol's indicators in its own session hours (TVP-1.3); one without lines can't be used.
  const indicatorIds = useMemo(
    () => serverIds.filter((id) => indicatorLines(id, 14).length > 0),
    [serverIds],
  );
  const update = (index: number, rule: ScreenerRuleInput) => onChange(rules.map((item, at) => (at === index ? rule : item)));
  const setMetric = (index: number, metric: ScreenerMetric) => {
    const rule = rules[index];
    if (metric === 'indicator') {
      const id = indicatorIds[0] ?? 'rsi';
      update(index, { ...rule, metric, source: { kind: 'indicator', indicator_id: id, inputs: { period: rule.period ?? 14 }, output: indicatorLines(id, rule.period ?? 14)[0]?.key ?? '' } as never });
    } else {
      update(index, { ...rule, metric, source: null });
    }
  };
  return (
    <fieldset className="trading-screener-rules">
      <legend>Filters and columns</legend>
      {rules.map((rule, index) => {
        const uses = SCREENER_METRICS.find((item) => item.value === rule.metric)?.uses ?? 'none';
        const isColumn = rule.role === 'column';
        return (
          <div key={rule.rule_id} className="trading-screener-rule" role="group" aria-label={`Rule ${index + 1}`}>
            <select aria-label={`Role of ${rule.rule_id}`} value={rule.role ?? 'filter'} onChange={(event) => update(index, { ...rule, role: event.target.value as 'filter' | 'column' })}>
              <option value="filter">Filter</option>
              <option value="column">Column</option>
            </select>
            <select aria-label={`Metric of ${rule.rule_id}`} value={rule.metric} onChange={(event) => setMetric(index, event.target.value as ScreenerMetric)}>
              {SCREENER_METRICS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            {uses === 'period' ? <input aria-label={`Period of ${rule.rule_id}`} inputMode="numeric" value={rule.period ?? 14} onChange={(event) => update(index, { ...rule, period: Number(event.target.value) || 1 })} /> : null}
            {uses === 'lookback' ? <input aria-label={`Bars of ${rule.rule_id}`} inputMode="numeric" value={rule.lookback_bars ?? 1} onChange={(event) => update(index, { ...rule, lookback_bars: Number(event.target.value) || 1 })} /> : null}
            {uses === 'indicator' ? <IndicatorFields rule={rule} indicatorIds={indicatorIds} onChange={(next) => update(index, next)} /> : null}
            {isColumn ? null : (
              <>
                <select aria-label={`Comparison of ${rule.rule_id}`} value={rule.operator} onChange={(event) => update(index, { ...rule, operator: event.target.value as ScreenerRuleInput['operator'] })}>
                  {SCREENER_OPERATORS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
                <input aria-label={`Value of ${rule.rule_id}`} inputMode="decimal" value={String(rule.threshold)} onChange={(event) => update(index, { ...rule, threshold: event.target.value })} />
              </>
            )}
            <button type="button" aria-label={`Remove rule ${index + 1}`} disabled={rules.length === 1} onClick={() => onChange(rules.filter((_, at) => at !== index))}>×</button>
          </div>
        );
      })}
      <div className="trading-screener-rule-actions">
        <button type="button" disabled={rules.length >= 20} onClick={() => onChange([...rules, newScreenerRule('filter')])}>Add filter</button>
        <button type="button" disabled={rules.length >= 20} onClick={() => onChange([...rules, newScreenerRule('column')])}>Add column</button>
      </div>
    </fieldset>
  );
}
