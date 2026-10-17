// Canvas host for drawings (TVP-0.4 renderer spike): a lightweight-charts
// series primitive that paints every drawing's shapes inside the chart's own
// render pass, so drawings can never lag the chart. The SVG overlay keeps
// input, handles and draft previews; this class only paints and hit-tests.
import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesPrimitive,
  PrimitiveHoveredItem,
  SeriesAttachedParameter,
  Time,
} from 'lightweight-charts';
import { DEFAULT_CANVAS_PAINT_THEME, paintShapes, type CanvasPaintTheme } from './canvasShapes';
import { hitTestDrawing } from './tools/scene';
import type { DrawingGeometryContext, DrawingShape, DrawingToolDefinition, ScreenPoint } from './tools/types';

export type CanvasDrawingEntry = {
  drawingId: string;
  definition: DrawingToolDefinition;
  shapes: readonly DrawingShape[];
  context: DrawingGeometryContext | null;
};

/** Builds the shapes to paint for a pane of the given size, at paint time. */
export type CanvasDrawingScene = (viewport: { width: number; height: number }) => readonly CanvasDrawingEntry[];

type RenderTarget = Parameters<IPrimitivePaneRenderer['draw']>[0];

export class DrawingCanvasPrimitive implements ISeriesPrimitive<Time> {
  private scene: CanvasDrawingScene = () => [];
  private painted: readonly CanvasDrawingEntry[] = [];
  private theme: CanvasPaintTheme = DEFAULT_CANVAS_PAINT_THEME;
  private requestUpdate: (() => void) | null = null;
  private readonly views: readonly IPrimitivePaneView[];

  constructor() {
    const renderer: IPrimitivePaneRenderer = {
      draw: (target: RenderTarget) => target.useMediaCoordinateSpace(({ context, mediaSize }) => {
        this.painted = this.scene(mediaSize);
        for (const entry of this.painted) paintShapes(context, entry.shapes, this.theme);
      }),
    };
    this.views = [{ zOrder: () => 'top', renderer: () => renderer }];
  }

  attached({ requestUpdate }: SeriesAttachedParameter<Time>): void {
    this.requestUpdate = requestUpdate;
  }

  detached(): void {
    this.requestUpdate = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  /** The overlay stylesheet's text paint (canvasShapes.readCanvasPaintTheme); repaints. */
  setTheme(theme: CanvasPaintTheme): void {
    this.theme = theme;
    this.invalidate();
  }

  setScene(scene: CanvasDrawingScene): void {
    this.scene = scene;
    this.invalidate();
  }

  /** Repaints on the chart's next frame (the chart repaints by itself on viewport changes). */
  invalidate(): void {
    this.requestUpdate?.();
  }

  /** The topmost painted drawing at a pane coordinate. */
  drawingAt(point: ScreenPoint): { drawingId: string; distance: number } | null {
    for (let index = this.painted.length - 1; index >= 0; index -= 1) {
      const entry = this.painted[index];
      if (!entry.context) continue;
      const hit = hitTestDrawing(entry.definition, entry.shapes, entry.context, point);
      if (hit) return { drawingId: entry.drawingId, distance: hit.distance };
    }
    return null;
  }

  hitTest(x: number, y: number): PrimitiveHoveredItem | null {
    const hit = this.drawingAt({ x, y });
    return hit
      ? { externalId: hit.drawingId, zOrder: 'top', cursorStyle: 'pointer', distance: hit.distance, hitTestPriority: 1, itemType: 'primitive' }
      : null;
  }
}
