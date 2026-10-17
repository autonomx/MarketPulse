import { afterEach, describe, expect, it } from 'vitest';
import { DRAWING_RENDERER_STORAGE_KEY, storedDrawingRendererMode } from './drawingRenderer';

describe('drawing renderer choice (TVP-3.4)', () => {
  afterEach(() => window.localStorage.removeItem(DRAWING_RENDERER_STORAGE_KEY));

  it('paints on the canvas unless SVG is chosen', () => {
    expect(storedDrawingRendererMode()).toBe('canvas');
    window.localStorage.setItem(DRAWING_RENDERER_STORAGE_KEY, 'svg');
    expect(storedDrawingRendererMode()).toBe('svg');
    window.localStorage.setItem(DRAWING_RENDERER_STORAGE_KEY, 'something else');
    expect(storedDrawingRendererMode()).toBe('canvas');
  });
});
