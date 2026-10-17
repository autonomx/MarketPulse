import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';
import { APPEARANCE_CHANGE_EVENT, onOmnixEvent } from '../../../events/bus';
import type { TradingChartAdapter } from '../chart/chartAdapter';
import { readCanvasPaintTheme } from './canvasShapes';
import type { TradingDrawing } from './drawingCommands';
import { DrawingCanvasPrimitive, type CanvasDrawingScene } from './DrawingCanvasPrimitive';

/**
 * Paints drawings through a chart series primitive instead of SVG elements
 * (renderer `canvas`). Because nothing is in the DOM, the overlay finds the
 * drawing under the pointer with the shapes' hit tests: in cursor mode it
 * takes pointer input only while the pointer is over a drawing, and marks
 * itself with that drawing's id so chart-level handlers treat the press as a
 * drawing press.
 */
export function useCanvasDrawingHost({
  enabled,
  adapter,
  svgRef,
  scene,
  drawingsRef,
}: {
  enabled: boolean;
  adapter: TradingChartAdapter | null;
  svgRef: RefObject<SVGSVGElement | null>;
  scene: CanvasDrawingScene;
  drawingsRef: RefObject<TradingDrawing[]>;
}) {
  const primitiveRef = useRef<DrawingCanvasPrimitive | null>(null);
  const sceneRef = useRef(scene);
  sceneRef.current = scene;

  useLayoutEffect(() => {
    if (!enabled || !adapter) return;
    const primitive = new DrawingCanvasPrimitive();
    primitive.setScene((viewport) => sceneRef.current(viewport));
    primitiveRef.current = primitive;
    const detach = adapter.attachPriceSeriesPrimitive(primitive);
    // Text colours and halo from the overlay's stylesheet, again whenever the appearance changes (once it applies).
    const readTheme = () => {
      if (svgRef.current) primitive.setTheme(readCanvasPaintTheme(svgRef.current));
    };
    readTheme();
    let frame = 0;
    const stopListening = onOmnixEvent(APPEARANCE_CHANGE_EVENT, () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(readTheme);
    });
    return () => {
      stopListening();
      cancelAnimationFrame(frame);
      detach();
      primitiveRef.current = null;
    };
  }, [adapter, enabled, svgRef]);

  // Any React render may have changed drawings, selection or previews.
  useEffect(() => {
    primitiveRef.current?.invalidate();
  });

  const drawingAt = (clientX: number, clientY: number): TradingDrawing | null => {
    const svg = svgRef.current;
    const primitive = primitiveRef.current;
    if (!svg || !primitive) return null;
    const bounds = svg.getBoundingClientRect();
    const hit = primitive.drawingAt({ x: clientX - bounds.left, y: clientY - bounds.top });
    return hit ? drawingsRef.current?.find((drawing) => drawing.drawingId === hit.drawingId) ?? null : null;
  };
  const drawingAtRef = useRef(drawingAt);
  drawingAtRef.current = drawingAt;

  useEffect(() => {
    const svg = svgRef.current;
    if (!enabled || !svg) return;
    const hover = (event: PointerEvent) => {
      if (event.buttons !== 0) return;
      const drawing = drawingAtRef.current(event.clientX, event.clientY);
      svg.style.pointerEvents = drawing ? 'auto' : '';
      svg.style.cursor = drawing ? 'pointer' : '';
      if (drawing) svg.dataset.drawingId = drawing.drawingId;
      else delete svg.dataset.drawingId;
    };
    window.addEventListener('pointermove', hover);
    return () => {
      window.removeEventListener('pointermove', hover);
      svg.style.pointerEvents = '';
      svg.style.cursor = '';
      delete svg.dataset.drawingId;
    };
  }, [enabled, svgRef]);

  return { drawingAt };
}
