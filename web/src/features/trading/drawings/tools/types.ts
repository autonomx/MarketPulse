// Drawing tool registry contract (TVP-0.4).
//
// A drawing tool is one `DrawingToolDefinition`: how it is created, which
// properties it has, and its geometry as renderer-agnostic shapes in screen
// space. Hosts (the SVG overlay, the canvas series primitive) render shapes and
// own input, selection, handles and undo; they never branch on a tool id.

import { guardToolCall } from './guard';

/**
 * A drawing anchor, stored in time/price space, never in pixels. Anchors of a
 * screen-anchored tool (`anchoring: 'screen'`) also carry `screen`, their
 * position as fractions (0-1) of the chart pane; they ignore time and price
 * when drawn. Stored anchors may carry other fields; keep them.
 */
export type DrawingPoint = { time: string; price: number; screen?: { x: number; y: number } };
export type DrawingStyle = { color: string; lineWidth: number; lineStyle: 'solid' | 'dashed' };
export const DEFAULT_DRAWING_STYLE: DrawingStyle = { color: '#66d9e8', lineWidth: 2, lineStyle: 'solid' };

export type ScreenPoint = { x: number; y: number };

/** One record of a list-of-records property, e.g. a fib level `{ value, color, visible }`. */
export type DrawingPropertyRecord = Readonly<Record<string, string | number | boolean>>;
export type DrawingPropertyValue = string | number | boolean | readonly number[] | readonly DrawingPropertyRecord[];
/**
 * Tool properties. Stored values are kept as they are, even when they don't
 * match the schema (a newer or older client may have written them), so
 * geometry reads them defensively (see `properties.ts`).
 */
export type DrawingProperties = Readonly<Record<string, DrawingPropertyValue>>;

export type DrawingRecordField =
  | { key: string; label: string; type: 'number'; min?: number; max?: number; step?: number }
  | { key: string; label: string; type: 'color' }
  | { key: string; label: string; type: 'boolean' }
  | { key: string; label: string; type: 'text' };

/** One editable tool-specific property; drives the generic properties dialog. */
export type DrawingPropertyField =
  | { key: string; label: string; type: 'boolean' }
  | { key: string; label: string; type: 'number'; min?: number; max?: number; step?: number }
  | { key: string; label: string; type: 'number-list'; min?: number; max?: number }
  | { key: string; label: string; type: 'select'; options: readonly { value: string; label: string }[] }
  | { key: string; label: string; type: 'color' }
  | { key: string; label: string; type: 'text' }
  | { key: string; label: string; type: 'records'; fields: readonly DrawingRecordField[]; newRecord: DrawingPropertyRecord };

/**
 * How a drawing is created.
 * - `click`: one anchor on pointer down.
 * - `drag`: two anchors, pointer down to pointer up.
 * - `click-click`: one anchor per click. With `anchors` the drawing completes on
 *   that click; without it, a double click completes it (at least `minAnchors`).
 *   Escape cancels.
 * - `freehand`: anchors follow the pointer while it is down, then are
 *   simplified (Ramer-Douglas-Peucker, `simplifyTolerance` in pixels).
 *
 * `snap: false` opts out of the chart's snap mode and keeps times between
 * bars; freehand defaults to `false`, every other gesture to `true`.
 */
export type DrawingCreation = { snap?: boolean } & (
  | { gesture: 'click' }
  | { gesture: 'drag' }
  | { gesture: 'click-click'; anchors?: number; minAnchors?: number }
  | { gesture: 'freehand'; minAnchors?: number; simplifyTolerance?: number }
);

/** Modifier keys held while an anchor is placed or dragged. */
export type DrawingModifiers = { shift: boolean; alt: boolean; ctrl: boolean };

/** Toolbar group ids of `TradingDrawingTools`. */
export type DrawingToolGroupId =
  | 'cursor'
  | 'lines'
  | 'channels'
  | 'pitchforks'
  | 'fibonacci'
  | 'gann'
  | 'chart-patterns'
  | 'elliott-waves'
  | 'cycles'
  | 'forecasting'
  | 'volume-based'
  | 'measurers'
  | 'brushes'
  | 'arrows'
  | 'shapes'
  | 'text-and-notes'
  | 'emojis';

/**
 * Paint and interaction of one shape.
 * - Colours are CSS colours. An undefined `fill` means no fill, except for text,
 *   where it means the renderer's default text colour.
 * - `className` is a theming hook for the DOM renderer (CSS may refine paint);
 *   every renderer must draw a correct shape from the other fields alone.
 * - `hit: 'none'` marks decoration: it never receives pointer input or hits.
 */
export type ShapePaint = {
  stroke?: string;
  strokeWidth?: number;
  dash?: readonly number[];
  fill?: string;
  fillOpacity?: number;
  opacity?: number;
  className?: string;
  hit?: 'auto' | 'none';
};

export type PathCommand =
  | { op: 'M' | 'L'; x: number; y: number }
  | { op: 'Q'; cx: number; cy: number; x: number; y: number }
  | { op: 'C'; c1x: number; c1y: number; c2x: number; c2y: number; x: number; y: number }
  | { op: 'Z' };

/** The closed set of shapes every renderer supports, in CSS pixels. */
export type DrawingShape =
  | ({ kind: 'segment'; x1: number; y1: number; x2: number; y2: number } & ShapePaint)
  | ({ kind: 'polyline'; points: readonly ScreenPoint[] } & ShapePaint)
  | ({ kind: 'polygon'; points: readonly ScreenPoint[] } & ShapePaint)
  | ({ kind: 'rect'; x: number; y: number; width: number; height: number; radius?: number } & ShapePaint)
  | ({ kind: 'ellipse'; cx: number; cy: number; rx: number; ry: number } & ShapePaint)
  | ({ kind: 'path'; commands: readonly PathCommand[] } & ShapePaint)
  | ({ kind: 'marker'; x: number; y: number; radius: number } & ShapePaint)
  | ({
    kind: 'text';
    x: number;
    y: number;
    text: string;
    align?: 'start' | 'middle' | 'end';
    fontSize?: number;
    fontWeight?: number;
    /**
     * Text over the chart gets a halo in the chart's background colour so it stays readable over lines (the default);
     * `false` for text on its own filled box (a price label, a measurement), which needs none.
     */
    halo?: boolean;
  } & ShapePaint);

export type DrawingShapeKind = DrawingShape['kind'];

/** A loaded bar, as numbers. */
export type DrawingBar = { time: string; open: number; high: number; low: number; close: number; volume: number };

/** The chart's loaded bars, converted on demand. */
export type DrawingBarSeries = {
  readonly length: number;
  at(index: number): DrawingBar | undefined;
  /** Index of the last bar at or before `time`; -1 before the first bar. */
  indexAtOrBefore(time: string): number;
};

export const EMPTY_DRAWING_BARS: DrawingBarSeries = { length: 0, at: () => undefined, indexAtOrBefore: () => -1 };

/** Chart services geometry may use besides the anchors. */
/** The instrument a drawing is on. */
export type DrawingInstrument = {
  /** Minimum price increment; null when unknown. */
  tickSize: number | null;
  /** Account currency per 1.0 price move per unit (1 for shares and spot crypto); null when unknown. */
  pointValue: number | null;
};

export const UNKNOWN_DRAWING_INSTRUMENT: DrawingInstrument = { tickSize: null, pointValue: null };

/**
 * Chart services for tool code (geometry, alert levels, handles, creation,
 * actions). Bar indices are the chart's own (`barTimeline.ts` spec): times
 * between bars interpolate, past the data they step by the interval.
 */
export type DrawingToolServices = {
  /** The chart bar index of a time (fractional between bars); null without bars. */
  barIndexForTime: (time: string) => number | null;
  /** The time of a chart bar index (inverse of barIndexForTime). */
  timeForBarIndex: (index: number) => string | null;
  /** The time `count` bars after `time` (fib time zones, cycles, position width). */
  timeAfterBars: (time: string, count: number) => string | null;
  /** Loaded bars (regression, anchored VWAP, volume profile, bars pattern, P&L). Its indices are positions in this list. */
  bars: DrawingBarSeries;
  /** The `bars` positions currently visible (first and last, inclusive), for tools that compute only what is visible; null when unknown. */
  visibleBars: () => { from: number; to: number } | null;
  /** A price as the price scale shows it. */
  formatPrice: (price: number) => string;
  instrument: DrawingInstrument;
};

export type DrawingChartAccess = DrawingToolServices & {
  /** Projects any time/price point, e.g. for levels between anchors. */
  project: (point: DrawingPoint) => ScreenPoint | null;
};

/** Everything a tool's geometry may depend on. Points are already projected. */
export type DrawingGeometryContext = DrawingChartAccess & {
  /** Anchors in CSS pixels; geometry runs only when every anchor projects. */
  points: readonly ScreenPoint[];
  /** The same anchors in time/price space. */
  rawPoints: readonly DrawingPoint[];
  viewport: { width: number; height: number };
  style: DrawingStyle;
  /** Tool properties; missing ones are filled from the defaults, stored ones kept as they are. */
  properties: DrawingProperties;
  text: string;
  interval: string;
  selected: boolean;
  locked: boolean;
  /** True while the drawing is still being created (anchors may be fewer than the tool's count, see `previewAnchors`). */
  draft: boolean;
};

export type DrawingHit = { distance: number };

/** Which way an alert line continues past its anchors. */
export type DrawingAlertExtend = 'none' | 'left' | 'right' | 'both';

/**
 * A line a drawing can alert on (TVP-1.4): two anchors, ordered by time, and
 * straight in bar-index space of the ALERT's interval (`barTimeline.ts` spec);
 * no clock-time slopes. A horizontal level is two anchors at one price.
 * Derived lines (a channel's parallel, a pitchfork's median) are expressed by
 * their own two anchors (`lineThroughPoint`). Shared fixtures:
 * resources/trading/drawing_alert_levels/.
 */
export type DrawingAlertLevel = {
  key: string;
  label: string;
  anchors: readonly [DrawingPoint, DrawingPoint];
  extend: DrawingAlertExtend;
  interpolation: 'bars';
};

/** A request a context action hands to the app (e.g. `{ type: 'order-ticket', payload: { entry, stop, target } }`). */
export type DrawingActionRequest = { type: string; payload: unknown };

/** What a tool's code sees of one drawing. */
export type DrawingSnapshot = {
  drawingId: string;
  instrumentId: string;
  points: readonly DrawingPoint[];
  properties: DrawingProperties;
  text: string;
};

/**
 * An action a tool offers in the chart context menu for one of its drawings.
 * Its request goes through the drawing action bus (`drawingActions.ts`) to
 * whichever feature handles that request type; no menu or panel code changes.
 */
export type DrawingContextAction = {
  id: string;
  label: string;
  request: (drawing: DrawingSnapshot, services: DrawingToolServices) => DrawingActionRequest;
};

/** A change to a drawing's anchors and/or properties (one undo step). */
export type DrawingEditPatch = { points?: readonly DrawingPoint[]; properties?: DrawingProperties };

/** What a handle's `drag` receives while it is dragged. */
export type DrawingHandleDrag = {
  points: readonly DrawingPoint[];
  properties: DrawingProperties;
  /** The pointer in chart space (snapped as the tool's creation snaps). */
  point: DrawingPoint;
  /** The pointer in pane pixels. */
  screen: ScreenPoint;
  modifiers: DrawingModifiers;
  services: DrawingToolServices;
};

/** An edit handle of a selected drawing, placed by geometry. */
export type DrawingHandle = {
  id: string;
  x: number;
  y: number;
  className?: string;
  /** Set for handles that move one anchor: the host applies the tool's Shift `constrain` against the other anchors. */
  anchorIndex?: number;
  drag: (input: DrawingHandleDrag) => DrawingEditPatch;
};

export type DrawingToolDefinition<Id extends string = string> = {
  id: Id;
  /** Toolbar label; matches a toolbar catalogue entry, which it makes available. */
  label: string;
  /** Name in the object tree when it differs from the label. */
  displayName?: string;
  /** Object tree icon: SVG path data in a 24 x 24 box (a default line icon when absent). */
  icon?: string;
  group: DrawingToolGroupId;
  creation: DrawingCreation;
  /** Screen-anchored tools (anchored note) stay put when the chart pans. */
  anchoring?: 'chart' | 'screen';
  defaultProperties: DrawingProperties;
  propertySchema: readonly DrawingPropertyField[];
  /** The drawing has user-editable text (the header shows a text input; the object tree shows the text). */
  editableText?: boolean;
  /** Text a new drawing starts with. */
  defaultText?: string;
  /** Draft preview while creating: an outline through the anchors (default) or the tool's own shapes. */
  draftPreview?: 'outline' | 'shapes';
  /** With `draftPreview: 'shapes'`, the anchor count (placed + pointer) from which geometry previews; default the tool's minimum. */
  previewAnchors?: number;
  /**
   * Edit handles of a selected drawing: one per anchor (`all`, the default),
   * the first and last anchor (`ends`, freehand), none, or the tool's own
   * (position tool stop/target/width, 8-handle rectangle, channel width).
   */
  handles?: 'all' | 'ends' | 'none' | ((context: DrawingGeometryContext) => readonly DrawingHandle[]);
  /** Extra class for the edit handles of a selected drawing. */
  handleClassName?: string;
  /** Adjusts a candidate anchor while placing or dragging it (e.g. Shift constrains to 45 degrees). */
  constrain?: (candidate: ScreenPoint, others: readonly ScreenPoint[], modifiers: DrawingModifiers) => ScreenPoint;
  geometry: (context: DrawingGeometryContext) => DrawingShape[];
  /** Overrides the default shape-derived hit test. */
  hitTest?: (shapes: readonly DrawingShape[], point: ScreenPoint, tolerance: number, context: DrawingGeometryContext) => DrawingHit | null;
  /** Levels for drawing alerts; a tool with alert levels offers alerts from its context menu. */
  alertLevels?: (points: readonly DrawingPoint[], properties: DrawingProperties, services: DrawingToolServices) => DrawingAlertLevel[];
  /** Fills in what clicks don't give a new drawing: extra anchors or properties (e.g. a position's default stop and target). */
  onCreate?: (anchors: readonly DrawingPoint[], services: DrawingToolServices) => { points: DrawingPoint[]; properties?: DrawingProperties };
  /** Extra context-menu actions (e.g. a position tool opens the order ticket). */
  contextActions?: readonly DrawingContextAction[];
};

/** Declares a tool and keeps its id as a literal type. */
export function defineDrawingTool<const Id extends string>(definition: DrawingToolDefinition<Id>): DrawingToolDefinition<Id> {
  return definition;
}

/** A handle that moves one anchor (the default handles). */
export function anchorHandle(index: number, position: ScreenPoint, className?: string): DrawingHandle {
  return {
    id: `anchor-${index}`,
    x: position.x,
    y: position.y,
    className,
    anchorIndex: index,
    drag: ({ points, point }) => ({ points: points.map((existing, position) => position === index ? { ...existing, ...point } : existing) }),
  };
}

/** Indices of the anchors that get edit handles with `all`/`ends`/`none`. */
export function handleIndices(definition: Pick<DrawingToolDefinition, 'handles'>, anchors: number): number[] {
  if (definition.handles === 'none' || typeof definition.handles === 'function' || anchors === 0) return [];
  if (definition.handles === 'ends') return anchors === 1 ? [0] : [0, anchors - 1];
  return Array.from({ length: anchors }, (_, index) => index);
}

/** The handles of a drawing whose anchors projected (`context`). */
export function drawingHandles(definition: DrawingToolDefinition, context: DrawingGeometryContext): readonly DrawingHandle[] {
  const { handles } = definition;
  if (typeof handles === 'function') return guardToolCall(definition.id, 'handles', () => handles(context), []);
  return handleIndices(definition, context.points.length).map((index) => anchorHandle(index, context.points[index], definition.handleClassName));
}

export function anchorCount(creation: DrawingCreation): { min: number; max: number | null } {
  switch (creation.gesture) {
    case 'click':
      return { min: 1, max: 1 };
    case 'drag':
      return { min: 2, max: 2 };
    case 'click-click':
      return creation.anchors !== undefined
        ? { min: creation.anchors, max: creation.anchors }
        : { min: creation.minAnchors ?? 2, max: null };
    case 'freehand':
      return { min: creation.minAnchors ?? 2, max: null };
  }
}

/** Whether creation snaps to the chart's snap mode and to bar times. */
export function creationSnaps(creation: DrawingCreation): boolean {
  return creation.snap ?? creation.gesture !== 'freehand';
}
