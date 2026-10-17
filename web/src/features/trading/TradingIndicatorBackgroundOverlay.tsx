import { useCallback, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import type { TradingChartAdapter, DrawingCoordinate } from './chart/chartAdapter';
import { autoPatternGroupKey, autoPatternTooltipDetails, type AutoPatternTooltipDetails } from './indicators/autoPatternTooltip';
import { indicatorPaneScale, type CoreIndicatorId, type IndicatorOutput } from './indicators/coreIndicators';
import './TradingIndicatorBackgroundOverlay.css';
import { chartPalette } from './chartPalette';

type TradingIndicatorBackgroundOverlayProps = {
  adapter: TradingChartAdapter;
  outputs: readonly IndicatorOutput[];
};

type HoveredAutoPattern = AutoPatternTooltipDetails & {
  x: number;
  y: number;
};

const AUTO_PATTERN_TOOLTIP_WIDTH = 252;
const AUTO_PATTERN_TOOLTIP_HEIGHT = 76;

function outputGroupKey(key: string): string {
  const parts = key.split(':');
  return parts.length > 1 ? parts.slice(0, -1).join(':') : key;
}

function boundaryOutputs(outputs: readonly IndicatorOutput[]): [IndicatorOutput, IndicatorOutput] | null {
  if (outputs.length < 2) return null;
  if (outputs.length === 2) return [outputs[0], outputs[1]];
  const upper = outputs.find((output) => output.key.endsWith(':upper'));
  const lower = outputs.find((output) => output.key.endsWith(':lower'));
  return upper && lower ? [upper, lower] : [outputs[0], outputs[outputs.length - 1]];
}

function bandPolygon(
  adapter: TradingChartAdapter,
  upper: IndicatorOutput,
  lower: IndicatorOutput,
): string | null {
  const lowerByTime = new Map(lower.points.map((point) => [point.time, point]));
  const first: DrawingCoordinate[] = [];
  const second: DrawingCoordinate[] = [];
  for (const point of upper.points) {
    const matchingPoint = lowerByTime.get(point.time);
    if (!matchingPoint) continue;
    const firstCoordinate = adapter.indicatorPointToCoordinate(upper.key, point);
    const secondCoordinate = adapter.indicatorPointToCoordinate(lower.key, matchingPoint);
    if (!firstCoordinate || !secondCoordinate) continue;
    first.push(firstCoordinate);
    second.push(secondCoordinate);
  }
  if (first.length < 2) return null;
  return [...first, ...second.reverse()].map(({ x, y }) => `${x},${y}`).join(' ');
}

type PaneBand = {
  id: string;
  key: string;
  scale: NonNullable<ReturnType<typeof indicatorPaneScale>>;
  color: string;
};

function formatPatternDate(value: string): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(date);
}

function directionLabel(direction: AutoPatternTooltipDetails['direction']): string {
  return direction[0].toUpperCase() + direction.slice(1);
}

export function TradingIndicatorBackgroundOverlay({ adapter, outputs }: TradingIndicatorBackgroundOverlayProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [hoveredPattern, setHoveredPattern] = useState<HoveredAutoPattern | null>(null);
  const groups = useMemo(() => {
    const grouped = new Map<string, IndicatorOutput[]>();
    for (const output of outputs) {
      if (output.kind !== 'line' || output.backgroundVisible === false) continue;
      const group = grouped.get(outputGroupKey(output.key)) ?? [];
      group.push(output);
      grouped.set(outputGroupKey(output.key), group);
    }
    return grouped;
  }, [outputs]);
  const patternOutputs = useMemo(
    () => outputs.filter((output) => output.kind === 'line' && output.points.length === 2 && autoPatternGroupKey(output.key) !== null),
    [outputs],
  );
  const paneBands = useMemo<PaneBand[]>(() => (
    [...groups.entries()].flatMap(([id, group]) => {
      const output = group.find((item) => item.pane === 1);
      const scale = indicatorPaneScale(id as CoreIndicatorId);
      if (!output || !scale?.band) return [];
      return [{ id, key: output.key, scale, color: output.backgroundColor ?? scale.band.color }];
    })
  ), [groups]);

  const refresh = useCallback(() => {
    const svg = svgRef.current;
    if (!svg) return;
    for (const polygon of svg.querySelectorAll<SVGPolygonElement>('[data-indicator-background]')) {
      const key = polygon.dataset.indicatorBackground;
      const group = key ? groups.get(key) : undefined;
      const boundaries = group ? boundaryOutputs(group) : null;
      if (!boundaries) {
        polygon.setAttribute('points', '');
        continue;
      }
      polygon.setAttribute('points', bandPolygon(adapter, boundaries[0], boundaries[1]) ?? '');
    }
    for (const bandElement of svg.querySelectorAll<SVGGElement>('[data-indicator-pane-band]')) {
      const id = bandElement.dataset.indicatorPaneBand;
      const band = paneBands.find((item) => item.id === id);
      if (!band?.scale.band) continue;
      const upper = adapter.indicatorValueToCoordinate(band.key, band.scale.band.to);
      const lower = adapter.indicatorValueToCoordinate(band.key, band.scale.band.from);
      if (upper === null || lower === null) continue;
      const width = adapter.indicatorPlotWidth();
      const rect = bandElement.querySelector<SVGRectElement>('[data-indicator-pane-band-fill]');
      if (rect) {
        rect.setAttribute('width', String(width));
        rect.setAttribute('y', String(Math.min(upper, lower)));
        rect.setAttribute('height', String(Math.abs(lower - upper)));
      }
      for (const line of bandElement.querySelectorAll<SVGLineElement>('[data-indicator-reference-line]')) {
        const value = Number(line.dataset.indicatorReferenceLine);
        const y = adapter.indicatorValueToCoordinate(band.key, value);
        if (y === null) continue;
        line.setAttribute('x2', String(width));
        line.setAttribute('y1', String(y));
        line.setAttribute('y2', String(y));
      }
    }
    for (const hitLine of svg.querySelectorAll<SVGLineElement>('[data-auto-pattern-hit]')) {
      const key = hitLine.dataset.autoPatternHit;
      const output = key ? patternOutputs.find((candidate) => candidate.key === key) : undefined;
      const first = output?.points[0];
      const last = output?.points[1];
      const from = first ? adapter.indicatorPointToCoordinate(output.key, first) : null;
      const to = last ? adapter.indicatorPointToCoordinate(output.key, last) : null;
      if (!from || !to) {
        hitLine.setAttribute('x1', '-1000');
        hitLine.setAttribute('x2', '-1000');
        hitLine.setAttribute('y1', '-1000');
        hitLine.setAttribute('y2', '-1000');
        continue;
      }
      hitLine.setAttribute('x1', String(from.x));
      hitLine.setAttribute('y1', String(from.y));
      hitLine.setAttribute('x2', String(to.x));
      hitLine.setAttribute('y2', String(to.y));
    }
  }, [adapter, groups, paneBands, patternOutputs]);

  useLayoutEffect(() => {
    refresh();
    return adapter.onViewportChange(refresh);
  }, [adapter, refresh]);

  useLayoutEffect(() => {
    if (!hoveredPattern) return;
    if (!patternOutputs.some((output) => autoPatternGroupKey(output.key) === hoveredPattern.groupKey)) setHoveredPattern(null);
  }, [hoveredPattern, patternOutputs]);

  const showPatternTooltip = (event: ReactPointerEvent<SVGLineElement>, output: IndicatorOutput) => {
    const svg = svgRef.current;
    const details = autoPatternTooltipDetails(output, outputs);
    if (!svg || !details) return;
    const rect = svg.getBoundingClientRect();
    const pointerX = event.clientX - rect.left;
    const pointerY = event.clientY - rect.top;
    const maxX = Math.max(6, rect.width - AUTO_PATTERN_TOOLTIP_WIDTH - 6);
    const maxY = Math.max(6, rect.height - AUTO_PATTERN_TOOLTIP_HEIGHT - 6);
    const preferredY = pointerY + 14 + AUTO_PATTERN_TOOLTIP_HEIGHT <= rect.height
      ? pointerY + 14
      : pointerY - AUTO_PATTERN_TOOLTIP_HEIGHT - 14;
    setHoveredPattern({
      ...details,
      x: Math.min(maxX, Math.max(6, pointerX + 14)),
      y: Math.min(maxY, Math.max(6, preferredY)),
    });
  };

  return (
    <svg ref={svgRef} className="trading-indicator-background-overlay" aria-hidden="true">
      {[...groups.entries()].filter(([, group]) => group.some((output) => output.pane === 0)).map(([key, group]) => {
        const boundaries = boundaryOutputs(group);
        if (!boundaries) return null;
        const [upper, lower] = boundaries;
        const points = bandPolygon(adapter, upper, lower);
        return <polygon key={key} data-indicator-background={key} points={points ?? ''} fill={upper.backgroundColor ?? lower.backgroundColor ?? chartPalette.sky} fillOpacity="0.2" />;
      })}
      {paneBands.map((band) => (
        <g key={band.id} data-indicator-pane-band={band.id}>
          <rect data-indicator-pane-band-fill="true" x="0" y="0" width="0" height="0" fill={band.color} fillOpacity="0.2" />
          {band.scale.levels.map((level) => (
            <line
              key={level.value}
              data-indicator-reference-line={String(level.value)}
              x1="0"
              x2="0"
              y1="0"
              y2="0"
              stroke={chartPalette.slateOverlay}
              strokeDasharray={level.lineStyle === 'dotted' ? '2 4' : '6 5'}
              strokeWidth="1"
            />
          ))}
        </g>
      ))}
      {patternOutputs.map((output) => (
        <line
          key={`hover:${output.key}`}
          className="trading-auto-pattern-hit"
          data-auto-pattern-hit={output.key}
          x1="-1000"
          y1="-1000"
          x2="-1000"
          y2="-1000"
          onPointerEnter={(event) => showPatternTooltip(event, output)}
          onPointerMove={(event) => showPatternTooltip(event, output)}
          onPointerLeave={() => setHoveredPattern(null)}
        />
      ))}
      {hoveredPattern ? (
        <g className={`trading-auto-pattern-tooltip is-${hoveredPattern.direction}`} transform={`translate(${hoveredPattern.x},${hoveredPattern.y})`}>
          <rect width={AUTO_PATTERN_TOOLTIP_WIDTH} height={AUTO_PATTERN_TOOLTIP_HEIGHT} rx="7" ry="7" />
          <circle cx="14" cy="17" r="4" />
          <text className="trading-auto-pattern-tooltip-title" x="25" y="21">{hoveredPattern.name}</text>
          <text className="trading-auto-pattern-tooltip-meta" x="12" y="43">
            {directionLabel(hoveredPattern.direction)}{hoveredPattern.confidence === null ? '' : ` · ${hoveredPattern.confidence}% confidence`}
          </text>
          <text className="trading-auto-pattern-tooltip-range" x="12" y="62">
            {formatPatternDate(hoveredPattern.fromTime)} → {formatPatternDate(hoveredPattern.toTime)}
          </text>
        </g>
      ) : null}
    </svg>
  );
}
