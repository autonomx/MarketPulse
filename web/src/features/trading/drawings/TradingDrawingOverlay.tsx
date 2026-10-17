import { useCallback, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import type { TradingChartAdapter } from '../chart/chartAdapter';
import type { CoreIndicatorId } from '../indicators/coreIndicators';
import type { TradingAlertIndicatorId } from '../tradingTypes';
import {
  type DrawingPoint,
  type DrawingSnapMode,
  type DrawingTool,
  type TradingDrawing,
} from './drawingCommands';
import {
  canvasScene,
  chartAccessFor,
  draftShapes,
  handleSignature,
  patchDraft,
  patchDrawings,
  renderDrawing,
  renderedHandles,
  svgViewport,
  type DrawingFrame,
  type RenderedDrawing,
} from './drawingFrame';
import { creationPointLocator, drawingPointLocator, locatePoint } from './drawingPointer';
import { storedDrawingRendererMode, type DrawingRendererMode } from './drawingRenderer';
import { ShapeElement } from './svgShapes';
import { drawingPropertiesWithDefaults, drawingToolDefinition, isDrawingToolId } from './tools/registry';
import { guardToolCall } from './tools/guard';
import { anchorProjector } from './tools/scene';
import { shapeSignature } from './tools/shapes';
import {
  UNKNOWN_DRAWING_INSTRUMENT,
  type DrawingActionRequest,
  type DrawingAlertLevel,
  type DrawingEditPatch,
  type DrawingHandle,
  type DrawingInstrument,
  type DrawingShape,
  type DrawingToolDefinition,
  type DrawingToolServices,
  type ScreenPoint,
} from './tools/types';
import { useCanvasDrawingHost } from './useCanvasDrawingHost';
import { useDrawingCreation, type PointerPoint } from './useDrawingCreation';
import { CLONE_GHOST_SUFFIX, cloneGhosts, useDrawingEditing, type TranslationPreview } from './useDrawingEditing';
import { toolDefaults } from './drawingTemplates';
import { drawingVisibleOnInterval } from './drawingVisibility';
import { useProjectionSync } from './useProjectionSync';
import './TradingDrawingMeasurement.css';

export type ChartAlertPlacement = DrawingPoint & {
  x: number;
  y: number;
  /** The chart indicator pane it was placed on, any indicator (TVP-1.3); `indicatorId` is only a legacy one. */
  chartIndicatorId?: string;
  source: 'tool' | 'context-menu';
  indicatorId?: TradingAlertIndicatorId;
  indicatorPeriod?: number;
  drawingId?: string;
  drawingTool?: DrawingTool;
  /**
   * Anchors for the chart's existing line alert, which the server extends both
   * ways: only for a drawing whose first alert level is unbounded or a segment
   * (`none`/`both`), never for a ray.
   */
  trendlinePoints?: DrawingPoint[];
  /** All alert levels the drawing's tool defines (TVP-1.4). */
  drawingAlertLevels?: DrawingAlertLevel[];
  /** Context-menu actions the drawing's tool offers, with their requests for the drawing action bus. */
  drawingActions?: readonly { id: string; label: string; request: DrawingActionRequest }[];
};

function newDrawing(toolType: TradingDrawing['toolType'], definition: DrawingToolDefinition, instrumentId: string, points: DrawingPoint[], services: DrawingToolServices): TradingDrawing {
  const { onCreate } = definition;
  const created = onCreate ? guardToolCall(definition.id, 'onCreate', () => onCreate(points, services), { points }) : { points };
  const defaults = toolDefaults(definition.id);
  return {
    drawingId: crypto.randomUUID(),
    instrumentId,
    toolType,
    points: created.points,
    selected: true,
    revision: 1,
    // The tool's last-used style and properties (TVP-3.8); what onCreate fills in wins.
    style: defaults.style,
    locked: false,
    hidden: false,
    text: definition.defaultText ?? '',
    properties: drawingPropertiesWithDefaults(definition.id, { ...defaults.properties, ...created.properties }),
  };
}

function ShapeGroup({ shapes }: { shapes: readonly DrawingShape[] }) {
  return (
    <g data-drawing-draft="" data-shape-signature={shapeSignature(shapes)} data-handle-signature="">
      {shapes.map((shape, index) => <ShapeElement key={index} shape={shape} index={index} />)}
    </g>
  );
}

function DrawingGroup({ item, withShapes, onPressDrawing, onPressHandle }: {
  item: RenderedDrawing;
  withShapes: boolean;
  onPressDrawing: (event: ReactPointerEvent<SVGElement>) => void;
  onPressHandle: (handle: DrawingHandle) => (event: ReactPointerEvent<SVGElement>) => void;
}) {
  const { drawing, shapes, selected } = item;
  const handles = selected && !drawing.locked ? renderedHandles(item) : [];
  return (
    <g
      data-drawing-id={drawing.drawingId}
      data-locked={drawing.locked}
      data-selected={selected}
      data-shape-signature={withShapes ? shapeSignature(shapes) : undefined}
      data-handle-signature={handleSignature(handles)}
      onPointerDown={onPressDrawing}
    >
      {withShapes ? shapes.map((shape, index) => <ShapeElement key={index} shape={shape} index={index} />) : null}
      {handles.map((handle) => (
        <circle
          key={handle.id}
          data-handle-id={handle.id}
          data-drawing-point-index={handle.anchorIndex}
          className={handle.className}
          cx={handle.x}
          cy={handle.y}
          r="6"
          onPointerDown={onPressHandle(handle)}
        />
      ))}
    </g>
  );
}

/** Wheel over the overlay zooms the chart at the pointer. */
function zoomOnWheel(adapter: TradingChartAdapter | null, event: React.WheelEvent<SVGSVGElement>): void {
  if (!adapter) return;
  const bounds = event.currentTarget.getBoundingClientRect();
  event.preventDefault();
  event.stopPropagation();
  adapter.zoomAtCoordinate(event.clientX - bounds.left, event.deltaY);
}

/** The pointer in pane pixels. */
function screenPoint(svg: SVGSVGElement | null, clientX: number, clientY: number): ScreenPoint | null {
  const bounds = svg?.getBoundingClientRect();
  return bounds ? { x: clientX - bounds.left, y: clientY - bounds.top } : null;
}

/** Where the anchors placed so far (all but the pointer's) are on screen now. */
function placedAnchorPositions(
  svg: SVGSVGElement | null,
  definition: DrawingToolDefinition | undefined,
  access: DrawingToolServices & { project: (point: DrawingPoint) => ScreenPoint | null },
  draft: readonly DrawingPoint[] | null,
): ScreenPoint[] {
  if (!definition || !svg || !draft) return [];
  const project = anchorProjector(definition, access.project, svgViewport(svg));
  return draft.slice(0, -1).map(project).filter((point): point is ScreenPoint => point !== null);
}

/** A chart placement at the pointer; over an indicator pane, its price is the indicator value. */
function chartPlacement(
  adapter: TradingChartAdapter | null,
  point: PointerPoint,
  clientY: number,
  source: ChartAlertPlacement['source'],
): { placement: ChartAlertPlacement; indicatorId: CoreIndicatorId | undefined } {
  const indicatorId = adapter?.indicatorPaneIdAtClientY(clientY) ?? undefined;
  const indicatorValue = indicatorId === undefined ? null : adapter?.indicatorValueFromClientY(indicatorId, clientY);
  return {
    placement: { x: point.x, y: point.y, time: point.time, price: indicatorValue ?? point.price, source },
    indicatorId,
  };
}

/** A well-formed alert level: two anchors with parseable times and finite prices. Tool output is not trusted. */
function isAlertLevel(value: unknown): value is DrawingAlertLevel {
  const level = value as Partial<DrawingAlertLevel> | null;
  return Boolean(level)
    && Array.isArray(level?.anchors)
    && level.anchors.length === 2
    && level.anchors.every((anchor) => Number.isFinite(anchor?.price) && Number.isFinite(Date.parse(anchor?.time)));
}

/**
 * The levels the menu may offer. A flat level crosses the same price whatever the bar index. A sloped one is offered
 * only when the server's bar index will match the drawn line: the chart plots the bars' own times
 * (`barIndexMatchesBars`) and the line starts within the loaded bars (before them the chart steps by the calendar,
 * the server by the bars it loads).
 */
export function offeredAlertLevels(levels: unknown, services: DrawingToolServices, barIndexMatchesBars: boolean): DrawingAlertLevel[] {
  if (!Array.isArray(levels)) return [];
  const firstBar = services.bars.length > 0 ? Date.parse(services.bars.at(0)?.time ?? '') : Number.NaN;
  return levels.filter(isAlertLevel).filter((level) => {
    const [first, second] = level.anchors;
    if (first.price === second.price) return true;
    const earliest = Math.min(Date.parse(first.time), Date.parse(second.time));
    return barIndexMatchesBars && Number.isFinite(firstBar) && earliest >= firstBar;
  });
}

/**
 * The keys of the alert levels a drawing has (TVP-1.4), whatever this chart can offer: a level that only extends
 * back in time doesn't count. An alert whose level is not among them has lost it.
 */
export function drawingAlertLevelKeys(drawing: TradingDrawing, services: DrawingToolServices): Set<string> {
  const alertLevels = drawingToolDefinition(drawing.toolType)?.alertLevels;
  if (!alertLevels) return new Set();
  const properties = drawingPropertiesWithDefaults(drawing.toolType, drawing.properties);
  const levels = guardToolCall(drawing.toolType, 'alertLevels', () => alertLevels(drawing.points, properties, services), []);
  return new Set((Array.isArray(levels) ? levels : []).filter((level) => level && level.extend !== 'left').map((level) => String(level.key)));
}

/**
 * What a drawing's tool offers from the context menu: its alert levels (see `offeredAlertLevels`) and actions.
 */
export function drawingMenuEntries(
  drawing: TradingDrawing | undefined,
  services: DrawingToolServices,
  barIndexMatchesBars = true,
): Partial<ChartAlertPlacement> {
  if (!drawing) return {};
  const definition = drawingToolDefinition(drawing.toolType);
  const properties = drawingPropertiesWithDefaults(drawing.toolType, drawing.properties);
  const alertLevels = definition?.alertLevels;
  const levels = alertLevels
    ? guardToolCall(drawing.toolType, 'alertLevels', () => offeredAlertLevels(alertLevels(drawing.points, properties, services), services, barIndexMatchesBars), [])
    : [];
  const alertable = levels.filter((level) => level.extend !== 'left');
  const legacyLine = alertable[0];
  const snapshot = { drawingId: drawing.drawingId, instrumentId: drawing.instrumentId, points: drawing.points, properties, text: drawing.text ?? '' };
  return {
    drawingId: drawing.drawingId,
    drawingTool: drawing.toolType,
    trendlinePoints: legacyLine?.anchors.map((point) => ({ time: point.time, price: point.price })),
    drawingAlertLevels: alertable.length > 0 ? alertable : undefined,
    drawingActions: definition?.contextActions?.flatMap((action) => {
      const request = guardToolCall(drawing.toolType, `contextActions.${action.id}`, () => action.request(snapshot, services), null);
      return request ? [{ id: action.id, label: action.label, request }] : [];
    }),
  };
}

export type TradingDrawingOverlayProps = {
  adapter: TradingChartAdapter | null;
  instrumentId: string;
  interval: string;
  tool: DrawingTool;
  snapMode: DrawingSnapMode;
  drawings: TradingDrawing[];
  selectedId: string | null;
  onAdd: (drawing: TradingDrawing) => void;
  onSelect: (id: string | null) => void;
  /** Ctrl+click on a drawing: add it to the selection or remove it (TVP-2.2). */
  onToggleSelect?: (id: string) => void;
  /** Ctrl+drag on a drawing: copies of it (or of the selection it is part of), moved. */
  onCloneDrawings?: (ids: readonly string[], from: DrawingPoint, to: DrawingPoint) => void;
  /** Ctrl+Alt+H: every drawing hidden (they stay stored and selectable from the object tree). */
  allHidden?: boolean;
  /** "Lock all drawings" (TVP-3.8): none can be moved or edited on the chart; each keeps its own lock. */
  allLocked?: boolean;
  onMovePoint: (id: string, index: number, point: DrawingPoint) => void;
  /** A handle's edit of anchors and/or properties; without it, single-anchor edits fall back to onMovePoint. */
  onEditDrawing?: (id: string, patch: DrawingEditPatch) => void;
  onTranslateDrawing: (id: string, from: DrawingPoint, to: DrawingPoint) => void;
  onRemove: (id: string) => void;
  onToolComplete?: () => void;
  onAlertAtPoint?: (placement: ChartAlertPlacement, indicatorId?: CoreIndicatorId) => void;
  onContextMenu?: (placement: ChartAlertPlacement, indicatorId?: CoreIndicatorId) => void;
  /** Tick size and point value for tools that need them (position tools). */
  instrument?: DrawingInstrument;
  /** Overrides the stored renderer switch (`drawingRenderer.ts`). */
  renderer?: DrawingRendererMode;
};

/** The drawings this chart shows (per-interval visibility), all locked while "Lock all drawings" is on. */
function chartDrawings(drawings: TradingDrawing[], interval: string, allLocked: boolean): TradingDrawing[] {
  const shown = drawings.filter((drawing) => drawingVisibleOnInterval(drawing.visibility, interval));
  return allLocked ? shown.map((drawing) => (drawing.locked ? drawing : { ...drawing, locked: true })) : shown;
}

/** The drawings a drag of `id` moves: the whole selection when `id` is part of a multi-selection. */
function selectionGroup(drawings: readonly TradingDrawing[], id: string): readonly string[] {
  // Locked drawings stay put, also in the preview.
  const selection = drawings.filter((drawing) => drawing.selected && (!drawing.locked || drawing.drawingId === id)).map((drawing) => drawing.drawingId);
  return selection.length > 1 && selection.includes(id) ? selection : [id];
}

/** The drawings the SVG draws: all visible ones (only the selected one when the canvas draws the rest), plus clone ghosts. */
function svgDrawings(drawings: readonly TradingDrawing[], selectedId: string | null, canvas: boolean, translation: TranslationPreview | null): TradingDrawing[] {
  return [
    ...drawings.filter((drawing) => !drawing.hidden && (!canvas || drawing.drawingId === selectedId)),
    ...cloneGhosts(drawings, translation),
  ];
}

/** onEditDrawing, or onMovePoint for an edit that moves exactly one anchor. */
function editFallback(drawings: TradingDrawing[], onMovePoint: TradingDrawingOverlayProps['onMovePoint']) {
  return (id: string, patch: DrawingEditPatch) => {
    const drawing = drawings.find((item) => item.drawingId === id);
    if (!drawing || !patch.points || patch.properties) return;
    const changed = patch.points.flatMap((point, index) => (point !== drawing.points[index] ? [index] : []));
    if (changed.length === 1) onMovePoint(id, changed[0], patch.points[changed[0]]);
  };
}

export function TradingDrawingOverlay({
  adapter,
  instrumentId,
  interval,
  tool,
  snapMode,
  drawings: storedDrawings, selectedId,
  onAdd,
  onSelect,
  onToggleSelect, onCloneDrawings, allHidden = false, allLocked = false,
  onMovePoint,
  onEditDrawing,
  onTranslateDrawing,
  onRemove,
  onToolComplete,
  onAlertAtPoint,
  onContextMenu: onChartContextMenu,
  instrument = UNKNOWN_DRAWING_INSTRUMENT,
  renderer,
}: TradingDrawingOverlayProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const drawings = chartDrawings(storedDrawings, interval, allLocked);
  const [storedRenderer] = useState(storedDrawingRendererMode);
  const canvas = (renderer ?? storedRenderer) === 'canvas';
  const [viewport, setViewport] = useState({ width: 0, height: 0, revision: 0 });
  const rerender = useCallback(() => setViewport((value) => ({ ...value, revision: value.revision + 1 })), []);
  const drawingTool = isDrawingToolId(tool) ? tool : null;
  const definition = drawingTool ? drawingToolDefinition(drawingTool) : undefined;
  const access = chartAccessFor(adapter, instrument);
  const plainPoint = (clientX: number, clientY: number) => locatePoint(svgRef.current, adapter, clientX, clientY, { snapMode, snap: true, screen: false });

  const editing = useDrawingEditing({
    tool,
    enabled: adapter !== null,
    pointFor: drawingPointLocator(svgRef, adapter, snapMode),
    screenFor: (clientX, clientY) => screenPoint(svgRef.current, clientX, clientY),
    services: () => access,
    groupOf: (id) => selectionGroup(drawings, id),
    onSelect, onToggleSelect, onCloneDrawings,
    onEdit: onEditDrawing ?? editFallback(drawings, onMovePoint),
    onTranslateDrawing,
    onRemove,
    onToolComplete,
  });
  const frameRef = useRef<DrawingFrame | null>(null);
  const creation = useDrawingCreation(definition, (points) => {
    if (!definition || !drawingTool) return;
    onAdd(newDrawing(drawingTool, definition, instrumentId, points, access));
    onToolComplete?.();
  }, () => {
    const svg = svgRef.current;
    if (svg && frameRef.current && !patchDraft(svg, frameRef.current, access)) rerender();
  });
  // Shift-constrain against where the placed anchors are now (the chart may have panned since).
  const creationPoint = creationPointLocator(svgRef, adapter, snapMode, definition, () => (
    placedAnchorPositions(svgRef.current, definition, access, creation.draftRef.current)
  ));

  const frame: DrawingFrame = {
    drawings: allHidden ? [] : drawings,
    selectedId,
    interval,
    handlePreview: editing.handlePreview,
    translationPreview: editing.translationPreview,
    draft: creation.draftRef.current && definition ? { definition, points: creation.draftRef.current } : null,
  };
  frameRef.current = frame;
  const drawingsRef = useRef(drawings);
  drawingsRef.current = drawings;
  const accessRef = useRef(access);
  accessRef.current = access;
  const canvasHost = useCanvasDrawingHost({
    enabled: canvas,
    adapter,
    svgRef,
    drawingsRef,
    scene: (size) => canvasScene(frameRef.current ?? frame, accessRef.current, size),
  });

  const refreshProjection = useCallback(() => {
    const svg = svgRef.current;
    if (svg && adapter && frameRef.current && !patchDrawings(svg, frameRef.current, accessRef.current, !canvas)) rerender();
  }, [adapter, canvas, rerender]);

  useProjectionSync(adapter, svgRef, refreshProjection, (width, height) => {
    setViewport((value) => ({ width, height, revision: value.revision + 1 }));
  });

  const onPointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    // Canvas renderer: presses on painted drawings edit them (SVG drawings handle their own).
    const painted = canvas && editing.edits ? canvasHost.drawingAt(event.clientX, event.clientY) : null;
    if (painted) {
      editing.dragDrawing(painted)(event);
      return;
    }
    if (tool === 'cursor') {
      if (event.target === event.currentTarget) onSelect(null);
      return;
    }
    if (tool === 'alert') {
      const point = plainPoint(event.clientX, event.clientY);
      if (!point) return;
      const { placement, indicatorId } = chartPlacement(adapter, point, event.clientY, 'tool');
      onAlertAtPoint?.(placement, indicatorId);
      onToolComplete?.();
      return;
    }
    const point = creationPoint(event);
    if (!point) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    creation.pointerDown(point);
  };

  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!creation.draftRef.current) return;
    const point = creationPoint(event);
    if (point) creation.pointerMove(point);
  };

  const onContextMenu = (event: React.MouseEvent<SVGSVGElement>) => {
    const point = plainPoint(event.clientX, event.clientY);
    event.preventDefault();
    event.stopPropagation();
    if (!point) return;
    const target = event.target instanceof Element ? event.target.closest<SVGElement>('[data-drawing-id]') : null;
    const drawingId = target?.dataset.drawingId ?? (canvas ? canvasHost.drawingAt(event.clientX, event.clientY)?.drawingId : undefined);
    const drawing = drawingId ? drawings.find((item) => item.drawingId === drawingId) : undefined;
    const { placement, indicatorId } = chartPlacement(adapter, point, event.clientY, 'context-menu');
    onChartContextMenu?.({ ...placement, ...drawingMenuEntries(drawing, access, adapter?.drawingBarIndexMatchesBars() ?? true) }, indicatorId);
  };

  void viewport.revision;
  // Measure like the imperative refresh does, so both always agree on shape structure.
  const size = svgRef.current ? svgViewport(svgRef.current) : { width: viewport.width, height: viewport.height };
  const rendered = (allHidden ? [] : svgDrawings(drawings, selectedId, canvas, editing.translationPreview))
    .map((drawing) => renderDrawing(drawing, frame, access, size)).filter((item): item is RenderedDrawing => item !== null);

  return (
    <svg
      ref={svgRef}
      className={`trading-drawing-overlay tool-${tool}`}
      aria-label="Interactive chart drawings and alert placement"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={creation.pointerUp}
      onDoubleClick={creation.doubleClick}
      onWheel={(event) => zoomOnWheel(adapter, event)}
      onContextMenu={onContextMenu}
    >
      {rendered.map((item) => (
        <DrawingGroup
          key={item.drawing.drawingId}
          item={item}
          withShapes={!canvas || item.drawing.drawingId.endsWith(CLONE_GHOST_SUFFIX)}
          onPressDrawing={editing.dragDrawing(item.drawing)}
          onPressHandle={(handle) => editing.dragHandle(item.drawing, handle)}
        />
      ))}
      {frame.draft ? <ShapeGroup shapes={draftShapes(frame.draft, access, size, interval)} /> : null}
    </svg>
  );
}
