// What the drawing hosts draw for one frame: every drawing's shapes and the
// creation draft's preview, plus the imperative patch that keeps the mounted
// SVG on the chart while it pans (TVP-0.4).
import type { TradingChartAdapter } from '../chart/chartAdapter';
import { DEFAULT_DRAWING_STYLE, type DrawingPoint, type TradingDrawing } from './drawingCommands';
import type { CanvasDrawingEntry } from './DrawingCanvasPrimitive';
import { patchShapeElement } from './svgShapes';
import { drawingPropertiesWithDefaults, drawingToolDefinition } from './tools/registry';
import { anchorProjector, drawingGeometry, staticChartAccess } from './tools/scene';
import { shapeSignature } from './tools/shapes';
import {
  anchorCount,
  drawingHandles,
  UNKNOWN_DRAWING_INSTRUMENT,
  type DrawingBarSeries,
  type DrawingChartAccess,
  type DrawingGeometryContext,
  type DrawingHandle,
  type DrawingInstrument,
  type DrawingShape,
  type DrawingToolDefinition,
  type ScreenPoint,
} from './tools/types';
import { previewDrawing, type HandlePreview, type TranslationPreview } from './useDrawingEditing';

export type Viewport = { width: number; height: number };

export type DrawingFrame = {
  drawings: TradingDrawing[];
  selectedId: string | null;
  interval: string;
  handlePreview: HandlePreview | null;
  translationPreview: TranslationPreview | null;
  /** The drawing being created; its points array is updated in place while the pointer moves. */
  draft: { definition: DrawingToolDefinition; points: readonly DrawingPoint[] } | null;
};

export type RenderedDrawing = {
  drawing: TradingDrawing;
  definition: DrawingToolDefinition;
  rawPoints: DrawingPoint[];
  shapes: DrawingShape[];
  context: DrawingGeometryContext | null;
  selected: boolean;
};

/** Bars loaded on first use, so frames whose tools never read bars don't touch them. */
function lazyBars(load: () => DrawingBarSeries): DrawingBarSeries {
  let series: DrawingBarSeries | null = null;
  const get = () => (series ??= load());
  return {
    get length() {
      return get().length;
    },
    at: (index) => get().at(index),
    indexAtOrBefore: (time) => get().indexAtOrBefore(time),
  };
}

/** The chart services tool code may use. */
export function chartAccessFor(adapter: TradingChartAdapter | null, instrument: DrawingInstrument = UNKNOWN_DRAWING_INSTRUMENT): DrawingChartAccess {
  if (!adapter) return staticChartAccess(() => null, { instrument });
  return {
    project: (point) => adapter.projectDrawingPoint(point),
    barIndexForTime: (time) => adapter.drawingBarIndexForTime(time),
    timeForBarIndex: (index) => adapter.drawingTimeForBarIndex(index),
    timeAfterBars: (time, count) => adapter.drawingTimeAfterBars(time, count),
    bars: lazyBars(() => adapter.drawingBars()),
    visibleBars: () => adapter.drawingVisibleBars(),
    formatPrice: (price) => adapter.formatDrawingPrice(price),
    instrument,
  };
}

/** Runs a drawing's tool geometry for the current chart projection. Unknown tools render nothing. */
export function renderDrawing(drawing: TradingDrawing, frame: DrawingFrame, access: DrawingChartAccess, viewport: Viewport): RenderedDrawing | null {
  const definition = drawingToolDefinition(drawing.toolType);
  if (!definition) return null;
  const preview = previewDrawing(drawing, frame.translationPreview, frame.handlePreview);
  const rawPoints = preview.points;
  const selected = drawing.selected || drawing.drawingId === frame.selectedId;
  const { shapes, context } = drawingGeometry({
    definition,
    ...access,
    rawPoints,
    viewport,
    style: drawing.style ?? DEFAULT_DRAWING_STYLE,
    properties: drawingPropertiesWithDefaults(drawing.toolType, preview.properties),
    text: drawing.text ?? '',
    interval: frame.interval,
    selected,
    locked: drawing.locked ?? false,
    draft: false,
  });
  return { drawing, definition, rawPoints, shapes, context, selected };
}

/** The edit handles of a rendered drawing (none until all its anchors project). */
export function renderedHandles(item: RenderedDrawing): readonly DrawingHandle[] {
  return item.context ? drawingHandles(item.definition, item.context) : [];
}

export function handleSignature(handles: readonly DrawingHandle[]): string {
  return handles.map((handle) => `${handle.id}|${handle.className ?? ''}`).join(';');
}

/**
 * The creation preview: the tool's own shapes once enough anchors are placed
 * (`draftPreview: 'shapes'`, from `previewAnchors`), otherwise a dashed outline
 * through the anchors and the pointer.
 */
export function draftShapes(draft: NonNullable<DrawingFrame['draft']>, access: DrawingChartAccess, viewport: Viewport, interval: string): DrawingShape[] {
  const { definition, points } = draft;
  const previewFrom = definition.previewAnchors ?? anchorCount(definition.creation).min;
  if (definition.draftPreview === 'shapes' && points.length >= previewFrom) {
    return drawingGeometry({
      definition,
      ...access,
      rawPoints: points,
      viewport,
      style: DEFAULT_DRAWING_STYLE,
      properties: drawingPropertiesWithDefaults(definition.id, undefined),
      text: definition.defaultText ?? '',
      interval,
      selected: false,
      locked: false,
      draft: true,
      minAnchors: previewFrom,
    }).shapes;
  }
  const project = anchorProjector(definition, access.project, viewport);
  const projected: ScreenPoint[] = [];
  for (const point of points) {
    const screen = project(point);
    if (!screen) return [];
    projected.push(screen);
  }
  if (projected.length < 2) return [];
  return projected.length === 2
    ? [{ kind: 'segment', x1: projected[0].x, y1: projected[0].y, x2: projected[1].x, y2: projected[1].y, className: 'draft' }]
    : [{ kind: 'polyline', points: projected, className: 'draft' }];
}

/** Every visible drawing's shapes for the canvas renderer, at paint time. */
export function canvasScene(frame: DrawingFrame, access: DrawingChartAccess, viewport: Viewport): CanvasDrawingEntry[] {
  return frame.drawings
    .filter((drawing) => !drawing.hidden)
    .map((drawing) => renderDrawing(drawing, frame, access, viewport))
    .filter((item): item is RenderedDrawing => item !== null)
    .map((item) => ({ drawingId: item.drawing.drawingId, definition: item.definition, shapes: item.shapes, context: item.context }));
}

export function svgViewport(svg: SVGSVGElement): Viewport {
  const bounds = svg.getBoundingClientRect();
  return { width: bounds.width || svg.clientWidth, height: bounds.height || svg.clientHeight };
}

/** Patches a mounted group's shapes and handles; false when the structure changed and React must render it. */
function patchGroup(group: SVGGElement, shapes: readonly DrawingShape[], checkStructure: boolean, handles: readonly DrawingHandle[]): boolean {
  if (checkStructure && shapeSignature(shapes) !== group.dataset.shapeSignature) return false;
  if ((group.dataset.handleSignature ?? '') !== handleSignature(handles)) return false;
  const byId = new Map(handles.map((handle) => [handle.id, handle]));
  for (const child of group.children) {
    const element = child as SVGElement;
    const { shapeIndex, handleId } = element.dataset;
    if (shapeIndex !== undefined) {
      const shape = shapes[Number(shapeIndex)];
      if (shape) patchShapeElement(element, shape);
    } else if (handleId !== undefined) {
      const handle = byId.get(handleId);
      if (handle) {
        element.setAttribute('cx', String(handle.x));
        element.setAttribute('cy', String(handle.y));
      }
    }
  }
  return true;
}

/** Patches only the creation preview (pointer moves while creating). */
export function patchDraft(svg: SVGSVGElement, frame: DrawingFrame, access: DrawingChartAccess): boolean {
  const group = svg.querySelector<SVGGElement>(':scope > g[data-drawing-draft]');
  if (!frame.draft) return group === null;
  if (!group) return false;
  return patchGroup(group, draftShapes(frame.draft, access, svgViewport(svg), frame.interval), true, []);
}

/**
 * Moves every mounted drawing, and the creation preview, to the current
 * projection without React, so they stay on the chart while it pans. Returns
 * false when a shape structure changed and React must re-render. With the
 * canvas renderer only edit handles are in the DOM (`shapesInDom` false).
 */
export function patchDrawings(svg: SVGSVGElement, frame: DrawingFrame, access: DrawingChartAccess, shapesInDom: boolean): boolean {
  const viewport = svgViewport(svg);
  const byId = new Map(frame.drawings.map((drawing) => [drawing.drawingId, drawing]));
  let inSync = true;
  for (const group of svg.querySelectorAll<SVGGElement>(':scope > g[data-drawing-id]')) {
    const drawing = byId.get(group.dataset.drawingId ?? '');
    const item = drawing ? renderDrawing(drawing, frame, access, viewport) : null;
    if (!item) continue;
    const handles = item.selected && !item.drawing.locked ? renderedHandles(item) : [];
    if (!patchGroup(group, item.shapes, shapesInDom, handles)) inSync = false;
  }
  return patchDraft(svg, frame, access) && inSync;
}
