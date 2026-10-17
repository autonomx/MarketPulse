// Where the pointer is on the chart, for creating and editing drawings: snap
// mode, bar snapping (tools may opt out), screen anchoring and Shift
// constraints all apply here.
import type { RefObject } from 'react';
import type { TradingChartAdapter } from '../chart/chartAdapter';
import { snapDrawingPoint, type DrawingSnapMode } from './drawingCommands';
import { svgViewport } from './drawingFrame';
import { guardToolCall } from './tools/guard';
import { drawingToolDefinition } from './tools/registry';
import { anchorProjector } from './tools/scene';
import { creationSnaps, type DrawingModifiers, type DrawingToolDefinition, type ScreenPoint } from './tools/types';
import type { PointerPoint } from './useDrawingCreation';
import { modifiersOf, type DrawingPointLocator } from './useDrawingEditing';

export type LocateOptions = {
  snapMode: DrawingSnapMode;
  /** Snap mode and bar times apply (false: freehand and other tools that opt out). */
  snap: boolean;
  /** Record pane fractions for screen-anchored tools. */
  screen: boolean;
  /** Adjusts the pointer position first (Shift constrain). */
  constrain?: (candidate: ScreenPoint) => ScreenPoint;
};

/** The chart point under the pointer, with its overlay coordinates. */
export function locatePoint(svg: SVGSVGElement | null, adapter: TradingChartAdapter | null, clientX: number, clientY: number, options: LocateOptions): PointerPoint | null {
  if (!svg || !adapter) return null;
  const bounds = svg.getBoundingClientRect();
  const { x, y } = (options.constrain ?? ((point) => point))({ x: clientX - bounds.left, y: clientY - bounds.top });
  const point = adapter.drawingPointFromCoordinate(x, y, { exactTime: !options.snap });
  if (!point) return null;
  const anchored = options.snap ? snapDrawingPoint(point, options.snapMode) : { time: point.time, price: point.price };
  const { width, height } = svgViewport(svg);
  return { ...anchored, x, y, ...(options.screen && width > 0 && height > 0 ? { screen: { x: x / width, y: y / height } } : {}) };
}

/** Locates anchors of existing drawings (handle drags, translation) with their tool's options. */
export function drawingPointLocator(svgRef: RefObject<SVGSVGElement | null>, adapter: TradingChartAdapter | null, snapMode: DrawingSnapMode): DrawingPointLocator {
  return (drawing, clientX, clientY, modifiers, anchorIndex) => {
    const tool = drawingToolDefinition(drawing.toolType);
    const svg = svgRef.current;
    const others = (): ScreenPoint[] => {
      const project = anchorProjector(tool ?? {}, (point) => adapter?.projectDrawingPoint(point) ?? null, svg ? svgViewport(svg) : { width: 0, height: 0 });
      return drawing.points.filter((_, index) => index !== anchorIndex).map(project).filter((point): point is ScreenPoint => point !== null);
    };
    const point = locatePoint(svg, adapter, clientX, clientY, {
      snapMode,
      snap: tool ? creationSnaps(tool.creation) : true,
      screen: tool?.anchoring === 'screen',
      constrain: anchorIndex !== undefined && tool?.constrain
        ? (candidate) => guardToolCall(tool.id, 'constrain', () => tool.constrain!(candidate, others(), modifiers), candidate)
        : undefined,
    });
    return point && { time: point.time, price: point.price, ...(point.screen ? { screen: point.screen } : {}) };
  };
}

type PointerEventLike = { clientX: number; clientY: number } & Parameters<typeof modifiersOf>[0];

/** Locates the next anchor of the drawing being created, constrained against the anchors placed so far. */
export function creationPointLocator(
  svgRef: RefObject<SVGSVGElement | null>,
  adapter: TradingChartAdapter | null,
  snapMode: DrawingSnapMode,
  definition: DrawingToolDefinition | undefined,
  placed: () => readonly ScreenPoint[],
): (event: PointerEventLike) => PointerPoint | null {
  return (event) => {
    if (!definition) return null;
    const anchors = placed();
    const modifiers: DrawingModifiers = modifiersOf(event);
    return locatePoint(svgRef.current, adapter, event.clientX, event.clientY, {
      snapMode,
      snap: creationSnaps(definition.creation),
      screen: definition.anchoring === 'screen',
      constrain: definition.constrain && definition.creation.gesture !== 'freehand'
        ? (candidate) => guardToolCall(definition.id, 'constrain', () => definition.constrain!(candidate, anchors, modifiers), candidate)
        : undefined,
    });
  };
}
