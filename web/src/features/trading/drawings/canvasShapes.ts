// Canvas 2D rendering of drawing shapes (TVP-0.4). Paints the same
// `DrawingShape[]` the SVG host renders, in CSS-pixel (media) space, with the
// overlay stylesheet's text colours and halo (`CanvasPaintTheme`).
import type { DrawingShape, PathCommand } from './tools/types';

const SELECTED_GLOW = '#ffd43b';
const HALO_WIDTH = 3;
// A CSS drop-shadow's blur and a canvas shadowBlur of the same number don't look the same over a chart; this scale
// matches the canvas glow to the SVG one by eye (TVP-3.4).
const GLOW_BLUR_SCALE = 2;

/**
 * What the SVG host takes from the overlay stylesheet, for the canvas: the colour of text that sets none, the halo
 * behind text, selected text's colour, the font, and the selection glow (a selected shape's drop-shadow). Read from
 * the overlay's styles (`readCanvasPaintTheme`), so both hosts follow the theme.
 */
export type CanvasPaintTheme = { text: string; halo: string; selectedText: string; fontFamily: string; glow: string; glowBlur: number };

export const DEFAULT_CANVAS_PAINT_THEME: CanvasPaintTheme = {
  text: '#e2e8f0', halo: '#07101b', selectedText: SELECTED_GLOW, fontFamily: 'sans-serif', glow: SELECTED_GLOW, glowBlur: 3,
};

/** A computed `filter: drop-shadow(<color> <x> <y> <blur>)`: its colour and blur. */
export function dropShadowGlow(filter: string): { color: string; blur: number } | null {
  const match = /drop-shadow\((.+?)\s+(-?[\d.]+)px\s+(-?[\d.]+)px\s+([\d.]+)px\)/.exec(filter);
  return match ? { color: match[1], blur: Number(match[4]) } : null;
}

function painted(color: string | undefined): color is string {
  return color !== undefined && color !== '' && color !== 'none' && color !== 'transparent' && color !== 'rgba(0, 0, 0, 0)';
}

/** The overlay stylesheet's paint for text, from a probe `<text>` in the overlay (plain and selected). */
export function readCanvasPaintTheme(svg: SVGSVGElement): CanvasPaintTheme {
  const probe = (tag: 'text' | 'line', className: string) => {
    const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
    if (className) element.setAttribute('class', className);
    svg.appendChild(element);
    const style = window.getComputedStyle(element);
    const paint = { fill: style.fill, stroke: style.stroke, fontFamily: style.fontFamily, filter: style.filter };
    element.remove();
    return paint;
  };
  const plain = probe('text', '');
  const selected = probe('text', 'selected');
  const glow = dropShadowGlow(probe('line', 'selected').filter ?? '');
  return {
    text: painted(plain.fill) ? plain.fill : DEFAULT_CANVAS_PAINT_THEME.text,
    halo: plain.stroke || 'none',
    selectedText: painted(selected.fill) ? selected.fill : DEFAULT_CANVAS_PAINT_THEME.selectedText,
    fontFamily: plain.fontFamily || DEFAULT_CANVAS_PAINT_THEME.fontFamily,
    glow: glow?.color ?? DEFAULT_CANVAS_PAINT_THEME.glow,
    glowBlur: glow?.blur ?? DEFAULT_CANVAS_PAINT_THEME.glowBlur,
  };
}

function tracePath(context: CanvasRenderingContext2D, commands: readonly PathCommand[]): void {
  for (const command of commands) {
    switch (command.op) {
      case 'M':
        context.moveTo(command.x, command.y);
        break;
      case 'L':
        context.lineTo(command.x, command.y);
        break;
      case 'Q':
        context.quadraticCurveTo(command.cx, command.cy, command.x, command.y);
        break;
      case 'C':
        context.bezierCurveTo(command.c1x, command.c1y, command.c2x, command.c2y, command.x, command.y);
        break;
      case 'Z':
        context.closePath();
        break;
    }
  }
}

function traceShape(context: CanvasRenderingContext2D, shape: Exclude<DrawingShape, { kind: 'text' }>): void {
  context.beginPath();
  switch (shape.kind) {
    case 'segment':
      context.moveTo(shape.x1, shape.y1);
      context.lineTo(shape.x2, shape.y2);
      break;
    case 'polyline':
    case 'polygon': {
      const { points } = shape;
      if (points.length === 0) break;
      context.moveTo(points[0].x, points[0].y);
      for (let index = 1; index < points.length; index += 1) context.lineTo(points[index].x, points[index].y);
      if (shape.kind === 'polygon') context.closePath();
      break;
    }
    case 'rect':
      if (shape.radius) context.roundRect(shape.x, shape.y, shape.width, shape.height, shape.radius);
      else context.rect(shape.x, shape.y, shape.width, shape.height);
      break;
    case 'ellipse':
      context.ellipse(shape.cx, shape.cy, Math.max(0, shape.rx), Math.max(0, shape.ry), 0, 0, Math.PI * 2);
      break;
    case 'path':
      tracePath(context, shape.commands);
      break;
    case 'marker':
      context.arc(shape.x, shape.y, Math.max(0, shape.radius), 0, Math.PI * 2);
      break;
  }
}

function paintShape(context: CanvasRenderingContext2D, shape: DrawingShape, theme: CanvasPaintTheme): void {
  const selected = shape.className?.split(' ').includes('selected') ?? false;
  context.globalAlpha = shape.opacity ?? 1;
  if (selected) {
    context.shadowColor = theme.glow;
    context.shadowBlur = theme.glowBlur * GLOW_BLUR_SCALE;
  }
  if (shape.kind === 'text') {
    context.font = `${shape.fontWeight ?? 400} ${shape.fontSize ?? 11}px ${theme.fontFamily}`;
    context.textAlign = shape.align === 'middle' ? 'center' : shape.align === 'end' ? 'right' : 'left';
    context.textBaseline = 'alphabetic';
    // As in SVG: the halo is painted first, under the text (paint-order: stroke), unless the text is on its own box.
    const halo = shape.stroke ?? (shape.halo === false ? 'none' : theme.halo);
    if (painted(halo)) {
      context.strokeStyle = halo;
      context.lineWidth = HALO_WIDTH;
      context.setLineDash([]);
      context.strokeText(shape.text, shape.x, shape.y);
    }
    // Selected text without its own colour turns the selection colour, as the stylesheet does.
    context.fillStyle = shape.fill ?? (selected ? theme.selectedText : theme.text);
    context.fillText(shape.text, shape.x, shape.y);
  } else {
    traceShape(context, shape);
    if (shape.fill !== undefined && shape.fill !== 'none' && shape.fill !== 'transparent') {
      context.globalAlpha = (shape.opacity ?? 1) * (shape.fillOpacity ?? 1);
      context.fillStyle = shape.fill;
      context.fill();
      context.globalAlpha = shape.opacity ?? 1;
    }
    if (shape.stroke !== undefined && shape.stroke !== 'none') {
      context.strokeStyle = shape.stroke;
      context.lineWidth = shape.strokeWidth ?? 1;
      context.setLineDash(shape.dash ? [...shape.dash] : []);
      context.stroke();
    }
  }
  if (selected) {
    context.shadowColor = 'transparent';
    context.shadowBlur = 0;
  }
}

/** Paints shapes in order. The context's state is restored afterwards. */
export function paintShapes(
  context: CanvasRenderingContext2D,
  shapes: readonly DrawingShape[],
  theme: CanvasPaintTheme = DEFAULT_CANVAS_PAINT_THEME,
): void {
  context.save();
  context.lineJoin = 'round';
  for (const shape of shapes) paintShape(context, shape, theme);
  context.restore();
}
