import { guardToolCall } from './guard';
import { DRAWING_HIT_TOLERANCE, hitTestShapes } from './hitTest';
import {
  anchorCount,
  EMPTY_DRAWING_BARS,
  type DrawingChartAccess,
  type DrawingGeometryContext,
  type DrawingHit,
  type DrawingPoint,
  type DrawingShape,
  type DrawingToolDefinition,
  type DrawingToolServices,
  type ScreenPoint,
  UNKNOWN_DRAWING_INSTRUMENT,
} from './types';

export type DrawingProjector = (point: DrawingPoint) => ScreenPoint | null;

export type DrawingShapeInput = Omit<DrawingGeometryContext, 'points'> & {
  definition: DrawingToolDefinition;
  /** Run geometry from this many anchors (draft previews); default the tool's minimum. */
  minAnchors?: number;
};

/** Chart access for code without a chart (tests, previews before the chart exists). */
export function staticChartAccess(project: DrawingProjector, services: Partial<DrawingToolServices> = {}): DrawingChartAccess {
  return {
    project,
    barIndexForTime: () => null,
    timeForBarIndex: () => null,
    timeAfterBars: () => null,
    bars: EMPTY_DRAWING_BARS,
    visibleBars: () => null,
    formatPrice: (price) => price.toLocaleString(undefined, { maximumFractionDigits: 6 }),
    instrument: UNKNOWN_DRAWING_INSTRUMENT,
    ...services,
  };
}

/** Projects an anchor: screen-anchored tools place it by its pane fractions, others by time and price. */
export function anchorProjector(definition: Pick<DrawingToolDefinition, 'anchoring'>, project: DrawingProjector, viewport: { width: number; height: number }): DrawingProjector {
  if (definition.anchoring !== 'screen') return project;
  return (point) => (point.screen ? { x: point.screen.x * viewport.width, y: point.screen.y * viewport.height } : project(point));
}

/**
 * Projects a drawing's anchors and runs its geometry. Returns no shapes (and
 * a null context) until the tool has its minimum anchors and all of them
 * project onto the chart.
 */
export function drawingGeometry(input: DrawingShapeInput): { shapes: DrawingShape[]; context: DrawingGeometryContext | null } {
  const { definition, minAnchors, ...rest } = input;
  if (rest.rawPoints.length < (minAnchors ?? anchorCount(definition.creation).min)) return { shapes: [], context: null };
  const projectAnchor = anchorProjector(definition, rest.project, rest.viewport);
  const points: ScreenPoint[] = [];
  for (const point of rest.rawPoints) {
    const projected = projectAnchor(point);
    if (!projected) return { shapes: [], context: null };
    points.push(projected);
  }
  const context: DrawingGeometryContext = { ...rest, points };
  return { shapes: guardToolCall(definition.id, 'geometry', () => definition.geometry(context), []), context };
}

/** Hit-tests one drawing's shapes, through the tool's override when it has one. */
export function hitTestDrawing(
  definition: DrawingToolDefinition,
  shapes: readonly DrawingShape[],
  context: DrawingGeometryContext,
  point: ScreenPoint,
  tolerance = DRAWING_HIT_TOLERANCE,
): DrawingHit | null {
  const { hitTest } = definition;
  return hitTest
    ? guardToolCall(definition.id, 'hitTest', () => hitTest(shapes, point, tolerance, context), null)
    : hitTestShapes(shapes, point, tolerance);
}
