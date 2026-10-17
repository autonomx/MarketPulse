import { render } from '@testing-library/react';
import { createElement } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { dropShadowGlow, paintShapes, readCanvasPaintTheme } from './canvasShapes';
import { DrawingCanvasPrimitive, type CanvasDrawingEntry } from './DrawingCanvasPrimitive';
import { runTool } from '../../../test/drawingTools';
import { drawingToolDefinition } from './tools/registry';

function recordingContext() {
  const calls: string[] = [];
  const state: Record<string, unknown> = {};
  const record = (name: string) => (...args: unknown[]) => {
    calls.push(`${name}(${args.map((arg) => (Array.isArray(arg) ? `[${arg.join(',')}]` : String(arg))).join(',')})`);
  };
  const context = new Proxy(state, {
    get: (target, key: string) => (key in target ? target[key] : record(key)),
    set: (target, key: string, value) => {
      target[key] = value;
      calls.push(`${key}=${String(value)}`);
      return true;
    },
  });
  return { context: context as unknown as CanvasRenderingContext2D, calls };
}

function entry(toolId: string, pixels: [number, number][], drawingId = toolId): CanvasDrawingEntry {
  const { shapes, context } = runTool(toolId, pixels);
  return { drawingId, definition: drawingToolDefinition(toolId)!, shapes, context };
}

describe('canvas drawing renderer', () => {
  it('paints the same shapes as the SVG host: strokes, dashes, fills and text', () => {
    const { context, calls } = recordingContext();
    paintShapes(context, [
      { kind: 'segment', x1: 1, y1: 2, x2: 3, y2: 4, stroke: '#fff', strokeWidth: 2, dash: [6, 4] },
      { kind: 'rect', x: 0, y: 0, width: 10, height: 5, fill: '#66d9e8', fillOpacity: 0.5, radius: 2 },
      { kind: 'text', x: 5, y: 6, text: 'label', align: 'middle', fontSize: 13, fontWeight: 500 },
    ]);
    expect(calls).toEqual(expect.arrayContaining([
      'moveTo(1,2)', 'lineTo(3,4)', 'strokeStyle=#fff', 'lineWidth=2', 'setLineDash([6,4])', 'stroke()',
      'roundRect(0,0,10,5,2)', 'globalAlpha=0.5', 'fillStyle=#66d9e8', 'fill()',
      'font=500 13px sans-serif', 'textAlign=center', 'fillText(label,5,6)',
    ]));
    expect(calls[0]).toBe('save()');
    expect(calls.at(-1)).toBe('restore()');
  });

  it("paints text like the stylesheet does: the theme's colour and font, a halo under it, none on a boxed label (TVP-3.4)", () => {
    const theme = { text: 'rgb(1, 2, 3)', halo: 'rgb(255, 255, 255)', selectedText: 'rgb(9, 9, 9)', fontFamily: 'Inter', glow: 'rgb(255, 212, 59)', glowBlur: 3 };
    const { context, calls } = recordingContext();
    paintShapes(context, [
      { kind: 'text', x: 1, y: 2, text: 'plain' },
      { kind: 'text', x: 1, y: 2, text: 'boxed', fill: '#ffffff', halo: false },
      { kind: 'text', x: 1, y: 2, text: 'emoji', stroke: 'none' },
      { kind: 'text', x: 1, y: 2, text: 'picked', className: 'selected' },
      { kind: 'segment', x1: 0, y1: 0, x2: 1, y2: 1, stroke: '#fff', className: 'selected' },
    ], theme);
    const plain = calls.indexOf('fillText(plain,1,2)');
    // The halo is stroked first, then the text is filled over it.
    expect(calls.slice(0, plain)).toEqual(expect.arrayContaining(['font=400 11px Inter', 'strokeStyle=rgb(255, 255, 255)', 'lineWidth=3', 'strokeText(plain,1,2)', 'fillStyle=rgb(1, 2, 3)']));
    expect(calls.indexOf('strokeText(plain,1,2)')).toBeLessThan(plain);
    expect(calls).not.toContain('strokeText(boxed,1,2)');
    expect(calls).not.toContain('strokeText(emoji,1,2)');
    // Selected text without a colour of its own takes the selection colour; selected shapes glow.
    const picked = calls.indexOf('fillText(picked,1,2)');
    expect(calls.slice(plain, picked)).toEqual(expect.arrayContaining(['shadowColor=rgb(255, 212, 59)', 'shadowBlur=6', 'fillStyle=rgb(9, 9, 9)']));
  });

  it("reads the theme from the overlay's stylesheet through probe elements", () => {
    const view = render(createElement('svg'));
    const svg = view.container.querySelector('svg')!;
    const style = document.createElement('style');
    style.textContent = 'text { fill: rgb(1, 2, 3); stroke: rgb(4, 5, 6); font-family: Inter } text.selected { fill: rgb(7, 8, 9) } line.selected { filter: drop-shadow(rgb(255, 212, 59) 0px 0px 3px) }';
    document.head.appendChild(style);
    try {
      expect(readCanvasPaintTheme(svg)).toEqual({
        text: 'rgb(1, 2, 3)', halo: 'rgb(4, 5, 6)', selectedText: 'rgb(7, 8, 9)', fontFamily: 'Inter', glow: 'rgb(255, 212, 59)', glowBlur: 3,
      });
      expect(svg.childNodes).toHaveLength(0);
    } finally {
      style.remove();
      view.unmount();
    }
    expect(dropShadowGlow('none')).toBeNull();
  });

  it('paints the scene at draw time and hit-tests what it painted, topmost first', () => {
    const primitive = new DrawingCanvasPrimitive();
    const requestUpdate = vi.fn();
    primitive.attached({ requestUpdate } as never);
    const scene = vi.fn(() => [entry('rectangle', [[100, 100], [300, 300]], 'below'), entry('trend-line', [[100, 200], [300, 200]], 'above')]);
    primitive.setScene(scene);
    expect(requestUpdate).toHaveBeenCalledTimes(1);

    const { context, calls } = recordingContext();
    const target = { useMediaCoordinateSpace: (paint: (scope: unknown) => void) => paint({ context, mediaSize: { width: 800, height: 600 } }) };
    const [view] = primitive.paneViews();
    expect(view.zOrder?.()).toBe('top');
    view.renderer()!.draw(target as never);
    expect(scene).toHaveBeenCalledWith({ width: 800, height: 600 });
    expect(calls).toContain('stroke()');

    expect(primitive.hitTest(200, 201)).toMatchObject({ externalId: 'above', cursorStyle: 'pointer', zOrder: 'top' });
    expect(primitive.hitTest(150, 150)).toMatchObject({ externalId: 'below' });
    expect(primitive.hitTest(500, 500)).toBeNull();
    primitive.invalidate();
    expect(requestUpdate).toHaveBeenCalledTimes(2);
    primitive.detached();
    primitive.invalidate();
    expect(requestUpdate).toHaveBeenCalledTimes(2);
  });
});
