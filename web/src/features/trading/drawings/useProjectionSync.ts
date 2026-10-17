import { useLayoutEffect, useRef, type RefObject } from 'react';
import type { TradingChartAdapter } from '../chart/chartAdapter';

/**
 * Keeps mounted drawings on the chart: viewport changes patch them in the same
 * callback; crosshair and pointer activity schedule a patch for the next frame.
 */
export function useProjectionSync(
  adapter: TradingChartAdapter | null,
  svgRef: RefObject<SVGSVGElement | null>,
  refreshProjection: () => void,
  resized: (width: number, height: number) => void,
) {
  const resizedRef = useRef(resized);
  resizedRef.current = resized;
  useLayoutEffect(() => {
    if (!adapter) return;
    let frameRequest: number | null = null;
    let pointerActive = false;
    const invalidate = () => {
      if (frameRequest !== null) return;
      frameRequest = window.requestAnimationFrame(() => {
        frameRequest = null;
        refreshProjection();
      });
    };
    // Project directly into the already-mounted SVG during the chart viewport
    // callback. Waiting for React to reconcile a revision leaves the overlay one
    // or more frames behind Lightweight Charts while the user is dragging.
    const viewportChange = adapter.onViewportChange(refreshProjection);
    const crosshair = adapter.onCrosshair(() => invalidate());
    const stage = svgRef.current?.parentElement;
    const insideStage = (target: EventTarget | null) => target instanceof Node && Boolean(stage?.contains(target));
    const pointerDown = (event: PointerEvent) => {
      if (!insideStage(event.target)) return;
      pointerActive = true;
      invalidate();
    };
    const pointerMove = (event: PointerEvent) => {
      if (pointerActive && insideStage(event.target)) invalidate();
    };
    const pointerUp = () => {
      if (!pointerActive) return;
      pointerActive = false;
      invalidate();
    };
    window.addEventListener('pointerdown', pointerDown);
    window.addEventListener('pointermove', pointerMove);
    window.addEventListener('pointerup', pointerUp);
    const resize = new ResizeObserver((entries) => {
      const bounds = entries[0]?.contentRect;
      if (!bounds) return;
      resizedRef.current(bounds.width, bounds.height);
    });
    if (svgRef.current) resize.observe(svgRef.current);
    refreshProjection();
    return () => {
      viewportChange();
      crosshair();
      window.removeEventListener('pointerdown', pointerDown);
      window.removeEventListener('pointermove', pointerMove);
      window.removeEventListener('pointerup', pointerUp);
      if (frameRequest !== null) window.cancelAnimationFrame(frameRequest);
      resize.disconnect();
    };
  }, [adapter, refreshProjection, svgRef]);
}
