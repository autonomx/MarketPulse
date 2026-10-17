import { AUTO_CHART_PATTERN_DEFINITIONS, isAutoChartPatternId, type AutoChartPatternId } from './autoPatterns';
import type { IndicatorOutput } from './coreIndicators';

export type AutoPatternTooltipDetails = {
  groupKey: string;
  id: Exclude<AutoChartPatternId, 'chart-patterns-all'>;
  name: string;
  direction: 'bullish' | 'bearish' | 'neutral';
  confidence: number | null;
  fromTime: string;
  toTime: string;
};

const bullishPatternIds = new Set<AutoChartPatternId>([
  'bullish-flag-pattern',
  'cup-handle-pattern',
  'double-bottom-pattern',
  'inverse-head-shoulders-pattern',
  'bullish-pennant-pattern',
  'triple-bottom-pattern',
  'falling-wedge-pattern',
]);

const bearishPatternIds = new Set<AutoChartPatternId>([
  'bearish-flag-pattern',
  'inverted-cup-handle-pattern',
  'double-top-pattern',
  'head-shoulders-pattern',
  'bearish-pennant-pattern',
  'triple-top-pattern',
  'rising-wedge-pattern',
]);

const definitionById = new Map(AUTO_CHART_PATTERN_DEFINITIONS.map((definition) => [definition.id, definition.name]));

export function autoPatternGroupKey(key: string): string | null {
  const [id, endIndex, segmentIndex, ...rest] = key.split(':');
  if (rest.length > 0 || !id || !endIndex || !segmentIndex) return null;
  if (!isAutoChartPatternId(id) || id === 'chart-patterns-all') return null;
  if (!/^\d+$/.test(endIndex) || !/^\d+$/.test(segmentIndex)) return null;
  return `${id}:${endIndex}`;
}

function directionFor(id: Exclude<AutoChartPatternId, 'chart-patterns-all'>, group: readonly IndicatorOutput[]): AutoPatternTooltipDetails['direction'] {
  if (bullishPatternIds.has(id)) return 'bullish';
  if (bearishPatternIds.has(id)) return 'bearish';
  if (id === 'triangle-pattern' || id === 'rectangle-pattern') return 'neutral';
  const color = group.find((output) => output.color)?.color?.trim().toLowerCase();
  if (color === '#20c997' || color === 'rgb(32, 201, 151)') return 'bullish';
  if (color === '#f23645' || color === 'rgb(242, 54, 69)') return 'bearish';
  return 'neutral';
}

function confidenceFor(group: readonly IndicatorOutput[]): number | null {
  for (const output of group) {
    const match = /·\s*(\d{1,3})%\s*$/u.exec(output.title);
    if (!match) continue;
    const value = Number(match[1]);
    if (Number.isFinite(value)) return Math.max(0, Math.min(100, value));
  }
  return null;
}

export function autoPatternTooltipDetails(
  output: IndicatorOutput,
  outputs: readonly IndicatorOutput[],
): AutoPatternTooltipDetails | null {
  const groupKey = autoPatternGroupKey(output.key);
  if (!groupKey) return null;
  const id = groupKey.slice(0, groupKey.lastIndexOf(':')) as Exclude<AutoChartPatternId, 'chart-patterns-all'>;
  const group = outputs.filter((candidate) => autoPatternGroupKey(candidate.key) === groupKey);
  if (group.length === 0) return null;
  const times = group
    .flatMap((candidate) => candidate.points.map((point) => point.time))
    .filter((time) => Number.isFinite(Date.parse(time)))
    .sort((left, right) => Date.parse(left) - Date.parse(right));
  if (times.length === 0) return null;
  return {
    groupKey,
    id,
    name: definitionById.get(id) ?? output.title.replace(/\s*·\s*\d{1,3}%\s*$/u, ''),
    direction: directionFor(id, group),
    confidence: confidenceFor(group),
    fromTime: times[0],
    toTime: times[times.length - 1],
  };
}
