// Which host paints drawings (TVP-0.4 renderer decision).
//
// Both hosts paint the same registry shapes. The canvas host (a Lightweight
// Charts series primitive, DrawingCanvasPrimitive) is the default: in the spike
// it cost about half as much per pan frame as SVG with 500 mixed drawings, it
// keeps the DOM flat, and it is clipped to the pane like the chart's own
// series. TVP-3.4 checked by eye that it paints like the themed SVG (text
// colour, halo, dot, light theme, selection glow; canvasShapes reads those from
// the overlay stylesheet) and made it the default. The SVG host stays as a
// fallback: set this localStorage key to `svg` and reload.
export type DrawingRendererMode = 'svg' | 'canvas';

export const DRAWING_RENDERER_STORAGE_KEY = 'omnix.trading.drawing-renderer';

export function storedDrawingRendererMode(): DrawingRendererMode {
  try {
    return window.localStorage.getItem(DRAWING_RENDERER_STORAGE_KEY) === 'svg' ? 'svg' : 'canvas';
  } catch {
    return 'canvas';
  }
}
