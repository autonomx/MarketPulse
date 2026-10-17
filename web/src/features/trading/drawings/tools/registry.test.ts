import { describe, expect, it } from 'vitest';
import { drawingToolGroups, drawingToolItemTool } from '../../TradingDrawingTools';
import type { DrawingTool } from '../drawingCommands';
import { hitTestShapes } from './hitTest';
import { DRAWING_TOOL_DEFINITIONS, drawingDisplayName, drawingPropertiesWithDefaults, drawingToolDefinition, isDrawingToolId } from './registry';
import { clampToField, propertyValueReadable } from './properties';
import { constrainTo45Degrees, shapeSignature, simplifyPolyline } from './shapes';
import { anchorCount } from './types';

// The 14 tools that existed before the registry, then TVP-3.x tools; each must keep a definition.
// Add new tools here as they are registered.
const DRAWING_TOOLS = [
  'dot', 'arrow', 'horizontal-line', 'horizontal-ray', 'trend-line', 'vertical-line', 'crossline',
  'ray', 'rectangle', 'circle', 'ellipse', 'fibonacci', 'text', 'measurement',
  // TVP-3.1 lines and channels
  'info-line', 'extended-line', 'trend-angle', 'parallel-channel', 'regression-trend', 'flat-top-bottom',
  'disjoint-channel', 'anchored-vwap',
  // TVP-3.6 positions and measurement
  'date-range', 'date-price-range', 'long-position', 'short-position', 'position-forecast', 'bars-pattern',
  'ghost-feed', 'sector', 'fixed-range-volume-profile',
  // TVP-3.2 Fibonacci, TVP-3.3 pitchforks and Gann
  'fib-extension', 'fib-time-zone', 'fib-time', 'fib-channel', 'fib-speed-fan', 'fib-arcs', 'fib-circles', 'fib-spiral', 'fib-wedge',
  'pitchfork', 'schiff-pitchfork', 'modified-schiff-pitchfork', 'inside-pitchfork', 'pitchfan', 'gann-box', 'gann-square-fixed', 'gann-square', 'gann-fan',
  // TVP-3.4 shapes and freehand, TVP-3.5 annotations, TVP-3.7 patterns, waves and cycles
  'brush', 'highlighter', 'path', 'polyline', 'curve', 'double-curve', 'triangle', 'rotated-rectangle', 'arc',
  'note', 'anchored-note', 'price-note', 'callout', 'comment', 'signpost', 'price-label', 'flag-mark', 'arrow-mark-up', 'arrow-mark-down',
  'arrow-mark-left', 'arrow-mark-right', 'arrow-marker', 'icon', 'emoji',
  'xabcd-pattern', 'cypher-pattern', 'abcd-pattern', 'triangle-pattern', 'three-drives-pattern', 'head-and-shoulders',
  'elliott-impulse-wave', 'elliott-correction-wave', 'elliott-triangle-wave', 'elliott-double-combo-wave', 'elliott-triple-combo-wave',
  'cyclic-lines', 'time-cycles', 'sine-line',
] as const satisfies readonly Exclude<DrawingTool, 'cursor' | 'alert' | 'eraser'>[];

describe('drawing tool registry', () => {
  it('has a definition for every drawing tool and nothing for the modes', () => {
    for (const tool of DRAWING_TOOLS) expect(drawingToolDefinition(tool)?.id).toBe(tool);
    for (const mode of ['cursor', 'alert', 'eraser']) expect(isDrawingToolId(mode)).toBe(false);
    expect(DRAWING_TOOL_DEFINITIONS).toHaveLength(DRAWING_TOOLS.length);
  });

  it('has unique ids and labels', () => {
    const ids = DRAWING_TOOL_DEFINITIONS.map((definition) => definition.id);
    const labels = DRAWING_TOOL_DEFINITIONS.map((definition) => definition.label);
    expect(new Set(ids).size).toBe(ids.length);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it('appears in its toolbar group under its label', () => {
    for (const definition of DRAWING_TOOL_DEFINITIONS) {
      const group = drawingToolGroups.find((item) => item.id === definition.group);
      const item = group?.items.find((candidate) => candidate.label === definition.label);
      expect(item && drawingToolItemTool(item), `${definition.id} in ${definition.group}`).toBe(definition.id);
    }
  });

  it('declares a schema entry for every default property', () => {
    for (const definition of DRAWING_TOOL_DEFINITIONS) {
      const keys = definition.propertySchema.map((field) => field.key);
      expect(Object.keys(definition.defaultProperties).sort()).toEqual([...keys].sort());
    }
  });

  it('keeps the creation gestures of the existing tools', () => {
    const gestures = Object.fromEntries(DRAWING_TOOL_DEFINITIONS.map((definition) => [definition.id, definition.creation.gesture]));
    expect(gestures).toMatchObject({
      dot: 'click', 'horizontal-line': 'click', 'horizontal-ray': 'click', 'vertical-line': 'click', crossline: 'click', text: 'click',
      arrow: 'drag', 'trend-line': 'drag', ray: 'drag', rectangle: 'drag', circle: 'drag', ellipse: 'drag', fibonacci: 'drag', measurement: 'drag',
    });
    expect(anchorCount({ gesture: 'click-click', anchors: 3 })).toEqual({ min: 3, max: 3 });
    expect(anchorCount({ gesture: 'click-click' })).toEqual({ min: 2, max: null });
    expect(anchorCount({ gesture: 'freehand', minAnchors: 2 })).toEqual({ min: 2, max: null });
  });

  it('fills missing properties and keeps stored ones, even of an unexpected shape', () => {
    const levels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1].map((value) => ({ value, color: '', visible: true }));
    expect(drawingPropertiesWithDefaults('fibonacci', undefined)).toEqual({ levels, showLabels: true, labelContent: 'levels', labelSide: 'right', reverse: false, extendLeft: false, extendRight: false });
    expect(drawingPropertiesWithDefaults('fibonacci', { levels: [0.5, 1], showLabels: false, future: 'kept' })).toEqual({
      levels: [0.5, 1], showLabels: false, future: 'kept', labelContent: 'levels', labelSide: 'right', reverse: false, extendLeft: false, extendRight: false,
    });
    expect(drawingPropertiesWithDefaults('unknown-tool', { a: 1 })).toEqual({ a: 1 });
  });

  it('names drawings for the object tree, text tools by their text', () => {
    expect(drawingDisplayName({ toolType: 'trend-line' })).toBe('Trendline');
    expect(drawingDisplayName({ toolType: 'ray' })).toBe('Ray');
    expect(drawingDisplayName({ toolType: 'text', text: '' })).toBe('Text note');
    expect(drawingDisplayName({ toolType: 'text', text: 'Breakout' })).toBe('Breakout');
    expect(drawingDisplayName({ toolType: 'pitchfork' })).toBe('Pitchfork');
    expect(drawingDisplayName({ toolType: 'warp-drive' })).toBe('warp-drive (unsupported)');
  });
});

describe('property helpers', () => {
  it('tells which stored values the settings dialog can edit, and clamps numbers', () => {
    const levels = { key: 'levels', label: 'Levels', type: 'records', fields: [], newRecord: {} } as const;
    expect(propertyValueReadable(levels, [{ value: 1 }])).toBe(true);
    expect(propertyValueReadable(levels, [0, 1])).toBe(false);
    expect(propertyValueReadable({ key: 'a', label: 'A', type: 'select', options: [{ value: 'x', label: 'X' }] }, 'y')).toBe(false);
    expect(clampToField(5, { min: 0, max: 1 })).toBe(1);
    expect(clampToField(-5, { min: 0 })).toBe(0);
    expect(clampToField(0.5, {})).toBe(0.5);
  });
});

describe('shape helpers', () => {
  it('ignores decoration in hit tests', () => {
    expect(hitTestShapes([{ kind: 'segment', x1: 0, y1: 0, x2: 10, y2: 0, stroke: '#fff', hit: 'none' }], { x: 5, y: 0 })).toBeNull();
    expect(hitTestShapes([{ kind: 'polyline', points: [{ x: 0, y: 0 }, { x: 10, y: 10 }], stroke: '#fff', strokeWidth: 2 }], { x: 5, y: 8 })).not.toBeNull();
    expect(hitTestShapes([{ kind: 'path', commands: [{ op: 'M', x: 0, y: 0 }, { op: 'Q', cx: 50, cy: 100, x: 100, y: 0 }], stroke: '#fff' }], { x: 50, y: 50 })).not.toBeNull();
  });

  it('Shift constrains a candidate anchor to 45 degrees from the previous one', () => {
    const none = { shift: false, alt: false, ctrl: false };
    expect(constrainTo45Degrees({ x: 100, y: 7 }, [{ x: 0, y: 0 }], none)).toEqual({ x: 100, y: 7 });
    const flat = constrainTo45Degrees({ x: 100, y: 7 }, [{ x: 0, y: 0 }], { ...none, shift: true });
    expect(flat.x).toBeCloseTo(100, 0);
    expect(flat.y).toBeCloseTo(0);
    const diagonal = constrainTo45Degrees({ x: 100, y: 90 }, [{ x: 0, y: 0 }], { ...none, shift: true });
    expect(diagonal.x).toBeCloseTo(diagonal.y);
  });

  it('signs shape structure by paint as well as kind', () => {
    const line = { kind: 'segment', x1: 0, y1: 0, x2: 1, y2: 1, stroke: '#fff' } as const;
    expect(shapeSignature([line])).toBe(shapeSignature([{ ...line, x2: 50 }]));
    expect(shapeSignature([line])).not.toBe(shapeSignature([{ ...line, stroke: '#000' }]));
    expect(shapeSignature([line])).not.toBe(shapeSignature([{ ...line, className: 'selected' }]));
  });

  it('simplifies freehand strokes and keeps their ends', () => {
    const stroke = Array.from({ length: 101 }, (_, index) => ({ x: index, y: index % 2 === 0 ? 0 : 0.2 }));
    const simplified = simplifyPolyline(stroke, 1);
    expect(simplified).toEqual([{ x: 0, y: 0 }, { x: 100, y: 0 }]);
    const corner = simplifyPolyline([{ x: 0, y: 0 }, { x: 50, y: 0 }, { x: 50, y: 50 }], 1);
    expect(corner).toHaveLength(3);
  });
});
