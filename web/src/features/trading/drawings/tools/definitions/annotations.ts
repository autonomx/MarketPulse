// Annotations (TVP-3.5): note, anchored note, price note, callout, comment, signpost, price label, flag mark, arrow
// marks and the arrow marker, icons and emojis. Icons are Omnix's own simple shapes and emojis are drawn as text in
// the system's emoji font; TradingView's sticker artwork is not reproduced (roadmap TVP-3.5).
import { numberProperty, stringProperty } from '../properties';
import { areaFill, lineStroke } from '../shapes';
import { defineDrawingTool, type DrawingGeometryContext, type DrawingShape, type ScreenPoint, type ShapePaint } from '../types';

const FONT_SIZE = 12;
const CHAR_WIDTH = 7;
const PADDING = 6;

/** The width of a one-line text box (the renderers have no text metrics; this errs wide). */
export function textBoxWidth(text: string, fontSize = FONT_SIZE): number {
  return Math.max(24, Array.from(text).length * CHAR_WIDTH * (fontSize / FONT_SIZE) + PADDING * 2);
}

function boxPaint(context: DrawingGeometryContext): ShapePaint {
  return { ...lineStroke(context), ...areaFill(context.style.color), fillOpacity: 0.85 };
}

/** A text box with its top-left at `at`; returns the box and its text. */
function textBox(context: DrawingGeometryContext, at: ScreenPoint, text: string, radius = 4): DrawingShape[] {
  const width = textBoxWidth(text);
  const height = FONT_SIZE + PADDING * 2;
  return [
    { kind: 'rect', x: at.x, y: at.y, width, height, radius, ...boxPaint(context), fill: context.style.color, fillOpacity: 0.2 },
    { kind: 'text', x: at.x + PADDING, y: at.y + PADDING + FONT_SIZE - 2, text, fontSize: FONT_SIZE, hit: 'none' },
  ];
}

function noteGeometry(context: DrawingGeometryContext, fallback: string): DrawingShape[] {
  const [anchor] = context.points;
  const text = context.text || fallback;
  const box = textBox(context, { x: anchor.x + 8, y: anchor.y - FONT_SIZE - PADDING * 2 - 10 }, text);
  return [{ kind: 'marker', x: anchor.x, y: anchor.y, radius: context.selected ? 5 : 4, fill: context.style.color, stroke: context.style.color }, ...box];
}

export const noteTool = defineDrawingTool({
  id: 'note',
  label: 'Note',
  displayName: 'Note',
  group: 'text-and-notes',
  creation: { gesture: 'click' },
  editableText: true,
  defaultText: 'Note',
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => noteGeometry(context, 'Note'),
});

export const anchoredNoteTool = defineDrawingTool({
  id: 'anchored-note',
  label: 'Anchored note',
  displayName: 'Anchored Note',
  group: 'text-and-notes',
  creation: { gesture: 'click' },
  // Fixed to the screen: it stays put when the chart pans or zooms.
  anchoring: 'screen',
  editableText: true,
  defaultText: 'Note',
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    const [anchor] = context.points;
    return textBox(context, anchor, context.text || 'Note');
  },
});

export const priceNoteTool = defineDrawingTool({
  id: 'price-note',
  label: 'Price note',
  displayName: 'Price Note',
  group: 'text-and-notes',
  // The price point, then where its label goes.
  creation: { gesture: 'click-click', anchors: 2 },
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    const [point, labelAt] = context.points;
    const price = context.formatPrice(context.rawPoints[0].price);
    const width = textBoxWidth(price);
    const height = FONT_SIZE + PADDING * 2;
    const left = labelAt.x >= point.x ? labelAt.x : labelAt.x - width;
    return [
      { kind: 'segment', x1: point.x, y1: point.y, x2: labelAt.x, y2: labelAt.y, ...lineStroke(context) },
      { kind: 'marker', x: point.x, y: point.y, radius: 3, fill: context.style.color, stroke: context.style.color },
      ...textBox(context, { x: left, y: labelAt.y - height / 2 }, price),
    ];
  },
});

export const calloutTool = defineDrawingTool({
  id: 'callout',
  label: 'Callout',
  displayName: 'Callout',
  group: 'text-and-notes',
  // The point it calls out, then the box.
  creation: { gesture: 'click-click', anchors: 2 },
  editableText: true,
  defaultText: 'Callout',
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    const [target, boxAt] = context.points;
    const text = context.text || 'Callout';
    const width = textBoxWidth(text);
    const height = FONT_SIZE + PADDING * 2;
    const center = { x: boxAt.x + width / 2, y: boxAt.y + height / 2 };
    // A pointer from the box's centre to the target, a third of the box wide.
    const dx = target.x - center.x;
    const dy = target.y - center.y;
    const length = Math.hypot(dx, dy) || 1;
    const half = Math.min(width, height) / 3;
    const base = [{ x: center.x - dy / length * half, y: center.y + dx / length * half }, { x: center.x + dy / length * half, y: center.y - dx / length * half }];
    return [{ kind: 'polygon', points: [base[0], target, base[1]], ...boxPaint(context), fill: context.style.color, fillOpacity: 0.2 }, ...textBox(context, boxAt, text)];
  },
});

export const commentTool = defineDrawingTool({
  id: 'comment',
  label: 'Comment',
  displayName: 'Comment',
  group: 'text-and-notes',
  creation: { gesture: 'click' },
  editableText: true,
  defaultText: 'Comment',
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    // A speech bubble whose tail points at the anchor.
    const [anchor] = context.points;
    const text = context.text || 'Comment';
    const height = FONT_SIZE + PADDING * 2;
    const top = { x: anchor.x - 10, y: anchor.y - height - 10 };
    const tail: DrawingShape = { kind: 'polygon', points: [{ x: anchor.x - 4, y: anchor.y - 10 }, anchor, { x: anchor.x + 6, y: anchor.y - 10 }], ...boxPaint(context), fill: context.style.color, fillOpacity: 0.2 };
    return [tail, ...textBox(context, top, text, 8)];
  },
});

export const signpostTool = defineDrawingTool({
  id: 'signpost',
  label: 'Signpost',
  displayName: 'Signpost',
  group: 'text-and-notes',
  creation: { gesture: 'click' },
  editableText: true,
  defaultText: 'Signpost',
  defaultProperties: { height: 48 },
  propertySchema: [{ key: 'height', label: 'Height', type: 'number', min: 16, max: 200, step: 4 }],
  geometry: (context) => {
    const [anchor] = context.points;
    const text = context.text || 'Signpost';
    const pole = numberProperty(context.properties, 'height', 48);
    const width = textBoxWidth(text);
    const height = FONT_SIZE + PADDING * 2;
    return [
      { kind: 'segment', x1: anchor.x, y1: anchor.y, x2: anchor.x, y2: anchor.y - pole, ...lineStroke(context) },
      ...textBox(context, { x: anchor.x - width / 2, y: anchor.y - pole - height }, text),
    ];
  },
});

export const priceLabelTool = defineDrawingTool({
  id: 'price-label',
  label: 'Price label',
  displayName: 'Price Label',
  group: 'text-and-notes',
  creation: { gesture: 'click' },
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    // A tag pointing left at the anchor, holding the anchor's price.
    const [anchor] = context.points;
    const text = context.formatPrice(context.rawPoints[0].price);
    const width = textBoxWidth(text);
    const half = (FONT_SIZE + PADDING * 2) / 2;
    const tip = 8;
    return [
      {
        kind: 'polygon',
        points: [anchor, { x: anchor.x + tip, y: anchor.y - half }, { x: anchor.x + tip + width, y: anchor.y - half }, { x: anchor.x + tip + width, y: anchor.y + half }, { x: anchor.x + tip, y: anchor.y + half }],
        ...lineStroke(context),
        fill: context.style.color,
        fillOpacity: 0.85,
      },
      { kind: 'text', x: anchor.x + tip + PADDING, y: anchor.y + FONT_SIZE / 2 - 2, text, fontSize: FONT_SIZE, fill: '#ffffff', halo: false, hit: 'none' },
    ];
  },
});

export const flagMarkTool = defineDrawingTool({
  id: 'flag-mark',
  label: 'Flag mark',
  displayName: 'Flag Mark',
  group: 'text-and-notes',
  creation: { gesture: 'click' },
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    const [anchor] = context.points;
    return [
      { kind: 'segment', x1: anchor.x, y1: anchor.y, x2: anchor.x, y2: anchor.y - 28, ...lineStroke(context), strokeWidth: 2 },
      { kind: 'polygon', points: [{ x: anchor.x, y: anchor.y - 28 }, { x: anchor.x + 18, y: anchor.y - 22 }, { x: anchor.x, y: anchor.y - 16 }], ...lineStroke(context), fill: context.style.color, fillOpacity: 0.9 },
    ];
  },
});

type Direction = 'up' | 'down' | 'left' | 'right';

/** A block arrow whose tip is at `tip`, pointing `direction`, 24 px long. */
function arrowMark(tip: ScreenPoint, direction: Direction): ScreenPoint[] {
  // Drawn pointing up (tip at the top), then rotated.
  const shape = [[0, 0], [10, 12], [4, 12], [4, 24], [-4, 24], [-4, 12], [-10, 12]];
  const rotate = { up: [1, 0, 0, 1], down: [-1, 0, 0, -1], left: [0, 1, -1, 0], right: [0, -1, 1, 0] }[direction];
  return shape.map(([x, y]) => ({ x: tip.x + rotate[0] * x + rotate[1] * y, y: tip.y + rotate[2] * x + rotate[3] * y }));
}

function arrowMarkTool<const Id extends string>(id: Id, label: string, direction: Direction) {
  return defineDrawingTool({
    id,
    label,
    displayName: label.replace(/(^|\s)\w/g, (letter) => letter.toUpperCase()),
    group: 'arrows',
    creation: { gesture: 'click' },
    editableText: true,
    defaultText: '',
    defaultProperties: {},
    propertySchema: [],
    geometry: (context) => {
      const [tip] = context.points;
      const points = arrowMark(tip, direction);
      const shapes: DrawingShape[] = [{ kind: 'polygon', points, ...lineStroke(context), fill: context.style.color, fillOpacity: 0.9 }];
      if (context.text) {
        const tail = { up: { x: tip.x, y: tip.y + 40 }, down: { x: tip.x, y: tip.y - 32 }, left: { x: tip.x + 30, y: tip.y + 4 }, right: { x: tip.x - 30, y: tip.y + 4 } }[direction];
        const align = direction === 'left' ? 'start' : direction === 'right' ? 'end' : 'middle';
        shapes.push({ kind: 'text', x: tail.x, y: tail.y, text: context.text, align, fontSize: FONT_SIZE, fill: context.style.color, hit: 'none' });
      }
      return shapes;
    },
  });
}

export const arrowMarkUpTool = arrowMarkTool('arrow-mark-up', 'Arrow mark up', 'up');
export const arrowMarkDownTool = arrowMarkTool('arrow-mark-down', 'Arrow mark down', 'down');
export const arrowMarkLeftTool = arrowMarkTool('arrow-mark-left', 'Arrow mark left', 'left');
export const arrowMarkRightTool = arrowMarkTool('arrow-mark-right', 'Arrow mark right', 'right');

export const arrowMarkerTool = defineDrawingTool({
  id: 'arrow-marker',
  label: 'Arrow marker',
  displayName: 'Arrow Marker',
  group: 'arrows',
  creation: { gesture: 'drag' },
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    // A block arrow from A to B: a shaft and a head.
    const [from, to] = context.points;
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const length = Math.hypot(dx, dy);
    if (length < 1) return [{ kind: 'marker', x: to.x, y: to.y, radius: 4, fill: context.style.color }];
    const ux = dx / length;
    const uy = dy / length;
    const head = Math.min(18, length * 0.6);
    const at = (along: number, across: number): ScreenPoint => ({ x: from.x + ux * along - uy * across, y: from.y + uy * along + ux * across });
    return [{
      kind: 'polygon',
      points: [at(0, -3), at(length - head, -4), at(length - head, -10), at(length, 0), at(length - head, 10), at(length - head, 4), at(0, 3)],
      ...lineStroke(context),
      fill: context.style.color,
      fillOpacity: 0.9,
    }];
  },
});

// Icons: Omnix's own shapes in a 24 x 24 box centred on the anchor.

const ICONS: Record<string, readonly (readonly [number, number])[]> = {
  star: [[12, 2], [15, 9], [22, 9], [16.5, 13.5], [18.5, 21], [12, 16.5], [5.5, 21], [7.5, 13.5], [2, 9], [9, 9]],
  diamond: [[12, 2], [22, 12], [12, 22], [2, 12]],
  triangle: [[12, 3], [22, 21], [2, 21]],
  square: [[3, 3], [21, 3], [21, 21], [3, 21]],
  check: [[3, 13], [6, 10], [10, 14], [18, 5], [21, 8], [10, 19]],
  cross: [[5, 2], [12, 9], [19, 2], [22, 5], [15, 12], [22, 19], [19, 22], [12, 15], [5, 22], [2, 19], [9, 12], [2, 5]],
  bolt: [[13, 2], [4, 14], [11, 14], [9, 22], [20, 9], [13, 9]],
  flag: [[5, 2], [7, 2], [7, 4], [20, 4], [16, 9], [20, 14], [7, 14], [7, 22], [5, 22]],
};

export const ICON_NAMES = Object.keys(ICONS);

export const iconTool = defineDrawingTool({
  id: 'icon',
  label: 'Icons',
  displayName: 'Icon',
  group: 'emojis',
  creation: { gesture: 'click' },
  defaultProperties: { icon: 'star', size: 24 },
  propertySchema: [
    { key: 'icon', label: 'Icon', type: 'select', options: ICON_NAMES.map((name) => ({ value: name, label: name[0].toUpperCase() + name.slice(1) })) },
    { key: 'size', label: 'Size', type: 'number', min: 8, max: 120, step: 2 },
  ],
  geometry: (context) => {
    const [anchor] = context.points;
    const outline = ICONS[stringProperty(context.properties, 'icon', 'star')] ?? ICONS.star;
    const scale = numberProperty(context.properties, 'size', 24) / 24;
    const points = outline.map(([x, y]) => ({ x: anchor.x + (x - 12) * scale, y: anchor.y + (y - 12) * scale }));
    return [{ kind: 'polygon', points, ...lineStroke(context), strokeWidth: 1, fill: context.style.color, fillOpacity: 0.9 }];
  },
});

export const EMOJIS = ['🚀', '🔥', '📈', '📉', '💰', '⚠️', '✅', '❌', '👀', '🎯', '💡', '⭐'];

export const emojiTool = defineDrawingTool({
  id: 'emoji',
  label: 'Emoji picker',
  displayName: 'Emoji',
  group: 'emojis',
  creation: { gesture: 'click' },
  defaultProperties: { emoji: '🚀', size: 24 },
  propertySchema: [
    { key: 'emoji', label: 'Emoji', type: 'select', options: EMOJIS.map((emoji) => ({ value: emoji, label: emoji })) },
    { key: 'size', label: 'Size', type: 'number', min: 8, max: 120, step: 2 },
  ],
  geometry: (context) => {
    const [anchor] = context.points;
    const size = numberProperty(context.properties, 'size', 24);
    return [
      // An invisible box so the emoji can be selected and dragged anywhere on it; outlined when selected.
      {
        kind: 'rect', x: anchor.x - size / 2, y: anchor.y - size / 2, width: size, height: size, fill: '#000000', fillOpacity: 0,
        ...(context.selected ? { stroke: context.style.color, strokeWidth: 1, dash: [3, 3] } : {}),
      },
      { kind: 'text', x: anchor.x, y: anchor.y + size * 0.35, text: stringProperty(context.properties, 'emoji', '🚀'), align: 'middle', fontSize: size, stroke: 'none', hit: 'none' },
    ];
  },
});
