// Screener rules (TVP-9.1): what a screen filters on and which columns it
// shows. A filter must match; a column is computed and shown only. Every rule
// is also a result column, read from `metrics["rule:<rule_id>"]`.
import type { TradingScannerDefinitionInput, TradingScannerResult, TradingScannerRule } from './scannerTypes';

export type ScreenerRuleInput = TradingScannerDefinitionInput['rules'][number];
export type ScreenerMetric = ScreenerRuleInput['metric'];

/** Each metric, its label, and which inputs it takes. */
export const SCREENER_METRICS: ReadonlyArray<{ value: ScreenerMetric; label: string; uses: 'none' | 'period' | 'lookback' | 'indicator' }> = [
  { value: 'close', label: 'Price', uses: 'none' },
  { value: 'percent_change', label: 'Change % over N bars', uses: 'lookback' },
  { value: 'volume', label: 'Volume', uses: 'none' },
  { value: 'relative_volume', label: 'Relative volume (N bars)', uses: 'period' },
  { value: 'gap_percent', label: 'Gap %', uses: 'none' },
  { value: 'high_distance_percent', label: '% from N-bar high (252 = 52 weeks)', uses: 'period' },
  { value: 'low_distance_percent', label: '% from N-bar low (252 = 52 weeks)', uses: 'period' },
  { value: 'sma', label: 'SMA', uses: 'period' },
  { value: 'ema', label: 'EMA', uses: 'period' },
  { value: 'rsi', label: 'RSI', uses: 'period' },
  { value: 'atr', label: 'ATR', uses: 'period' },
  { value: 'indicator', label: 'Indicator line', uses: 'indicator' },
  // US stocks' fundamentals from SEC filings (TVP-9.1), at the last close.
  { value: 'market_cap', label: 'Market cap (USD)', uses: 'none' },
  { value: 'pe_ratio', label: 'P/E (TTM)', uses: 'none' },
  { value: 'ps_ratio', label: 'P/S (TTM)', uses: 'none' },
  { value: 'pb_ratio', label: 'P/B', uses: 'none' },
  { value: 'eps_ttm', label: 'EPS (TTM)', uses: 'none' },
  { value: 'revenue_growth', label: 'Revenue growth % (YoY, TTM)', uses: 'none' },
  { value: 'net_margin', label: 'Net margin % (TTM)', uses: 'none' },
];

export const SCREENER_OPERATORS = [
  { value: 'gt', label: '>' }, { value: 'gte', label: '≥' }, { value: 'lt', label: '<' }, { value: 'lte', label: '≤' },
] as const;

let ruleSequence = 0;

export function newScreenerRule(role: 'filter' | 'column' = 'filter'): ScreenerRuleInput {
  ruleSequence += 1;
  return {
    rule_id: `rule-${Date.now().toString(36)}-${ruleSequence}`,
    metric: role === 'filter' ? 'percent_change' : 'volume',
    operator: 'gte',
    threshold: role === 'filter' ? '1' : '0',
    period: 14,
    lookback_bars: 1,
    role,
    source: null,
  };
}

/** A rule's column heading. */
export function screenerRuleLabel(rule: Pick<TradingScannerRule, 'metric' | 'period' | 'lookback_bars' | 'source'>): string {
  const metric = SCREENER_METRICS.find((item) => item.value === rule.metric);
  if (rule.metric === 'indicator' && rule.source && rule.source.kind === 'indicator') return rule.source.output;
  if (metric?.uses === 'period') return `${rule.metric.replace(/_/g, ' ')} ${rule.period}`;
  if (metric?.uses === 'lookback') return `change % ${rule.lookback_bars}`;
  return metric?.label ?? rule.metric;
}

/** The history the rules need (the server checks the same; this sets the screen's history to fit). */
export function screenerHistoryNeeded(rules: readonly ScreenerRuleInput[]): number {
  return Math.max(2, ...rules.map((rule) => {
    const metric = SCREENER_METRICS.find((item) => item.value === rule.metric);
    if (metric?.uses === 'lookback') return (rule.lookback_bars ?? 1) + 1;
    if (metric?.uses === 'period') return (rule.period ?? 14) + 1;
    if (metric?.uses === 'indicator') return 300;
    return 2;
  }));
}

/** A result's value for a rule: by rule id (TVP-9.1), else the metric's legacy key (runs from before it). */
export function screenerValue(result: TradingScannerResult, rule: TradingScannerRule): number | null {
  const legacy = rule.metric === 'percent_change' ? `percent_change:${rule.lookback_bars}`
    : ['sma', 'ema', 'rsi', 'atr'].includes(rule.metric) ? `${rule.metric}:${rule.period}` : rule.metric;
  const raw = result.metrics?.[`rule:${rule.rule_id}`] ?? result.metrics?.[legacy];
  const value = raw === undefined || raw === null ? Number.NaN : Number(raw);
  return Number.isFinite(value) ? value : null;
}

export type ScreenerSort = { key: string; direction: 'asc' | 'desc' };

/** Results sorted by a rule column, the symbol, or the score (missing values last). */
export function sortScreenerResults(
  results: readonly TradingScannerResult[],
  rules: readonly TradingScannerRule[],
  sort: ScreenerSort,
): TradingScannerResult[] {
  const rule = rules.find((item) => `rule:${item.rule_id}` === sort.key);
  const valueOf = (result: TradingScannerResult): number | string | null => (
    rule ? screenerValue(result, rule) : sort.key === 'symbol' ? result.instrument_id : sort.key === 'score' ? Number(result.score) : result.rank
  );
  const sign = sort.direction === 'asc' ? 1 : -1;
  return [...results].sort((left, right) => {
    const a = valueOf(left);
    const b = valueOf(right);
    if (a === null || b === null) return a === b ? 0 : a === null ? 1 : -1;
    return (typeof a === 'string' ? a.localeCompare(String(b)) : a - Number(b)) * sign;
  });
}
