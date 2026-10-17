import { afterEach, describe, expect, it } from 'vitest';
import { DEFAULT_DRAWING_STYLE } from './drawingCommands';
import {
  drawingTemplatePayload,
  parseDrawingTemplate,
  parseStyle,
  rememberToolDefaults,
  resetToolDefaults,
  styleProperties,
  toolDefaults,
} from './drawingTemplates';
import { DEFAULT_DRAWING_TOOL_SETTINGS, drawingScopeId, loadDrawingToolSettings, saveDrawingToolSettings } from './drawingToolSettings';
import { drawingVisibleOnInterval, onlyOnInterval, visibilityUnitOf } from './drawingVisibility';

afterEach(() => window.localStorage.clear());

describe('per-interval visibility (TVP-3.8)', () => {
  it('maps intervals to TradingView\'s units, 60 minutes and more as hours', () => {
    expect(visibilityUnitOf('15m')).toEqual({ unit: 'minutes', count: 15 });
    expect(visibilityUnitOf('240')).toEqual({ unit: 'hours', count: 4 });
    expect(visibilityUnitOf('1D')).toEqual({ unit: 'days', count: 1 });
    expect(visibilityUnitOf('3M')).toEqual({ unit: 'months', count: 3 });
    expect(visibilityUnitOf('100t')).toEqual({ unit: 'ticks', count: 100 });
    expect(visibilityUnitOf('nonsense')).toBeNull();
  });

  it('shows by unit and count range, and on everything when unset', () => {
    const visibility = { minutes: { visible: true, from: 1, to: 15 }, days: { visible: false } };
    expect(drawingVisibleOnInterval(visibility, '5m')).toBe(true);
    expect(drawingVisibleOnInterval(visibility, '30m')).toBe(false);
    expect(drawingVisibleOnInterval(visibility, '1d')).toBe(false);
    expect(drawingVisibleOnInterval(visibility, '1h')).toBe(true);
    expect(drawingVisibleOnInterval(undefined, '1d')).toBe(true);
    expect(drawingVisibleOnInterval(visibility, 'garbage')).toBe(true);
  });

  it('"only this interval" keeps just the current interval', () => {
    const only = onlyOnInterval('4h');
    expect(drawingVisibleOnInterval(only, '4h')).toBe(true);
    expect(drawingVisibleOnInterval(only, '1h')).toBe(false);
    expect(drawingVisibleOnInterval(only, '15m')).toBe(false);
    expect(drawingVisibleOnInterval(onlyOnInterval('100t'), '200t')).toBe(true);
  });
});

describe('drawing templates (TVP-3.8)', () => {
  const style = { color: '#ff0000', lineWidth: 3, lineStyle: 'dashed' as const };

  it('a tool remembers the last style and properties set on one of its drawings', () => {
    expect(toolDefaults('trend-line')).toEqual({ style: DEFAULT_DRAWING_STYLE, properties: {} });
    rememberToolDefaults({ toolType: 'trend-line', style, properties: { extendRight: true } });
    expect(toolDefaults('trend-line')).toEqual({ style, properties: { extendRight: true } });
    expect(toolDefaults('ray').style).toEqual(DEFAULT_DRAWING_STYLE);
    resetToolDefaults('trend-line');
    expect(toolDefaults('trend-line').style).toEqual(DEFAULT_DRAWING_STYLE);
  });

  it('named templates round-trip through the preset documents and refuse other kinds', () => {
    const payload = drawingTemplatePayload('  Red dashed  ', { toolType: 'trend-line', style, properties: { extendLeft: true } });
    expect(parseDrawingTemplate({ record_id: 'r1', status: 'active', payload } as never)).toEqual({
      recordId: 'r1', name: 'Red dashed', toolType: 'trend-line', style, properties: { extendLeft: true },
    });
    expect(parseDrawingTemplate({ record_id: 'r2', status: 'active', payload: { ...payload, templateKind: 'chart-template' } } as never)).toBeNull();
    expect(parseDrawingTemplate({ record_id: 'r3', status: 'archived', payload } as never)).toBeNull();
    expect(parseStyle({ color: '#fff', lineWidth: 'wide', lineStyle: 'solid' })).toBeNull();
  });
});

describe('template properties (TVP-3.8 review)', () => {
  it('carry only the settings of the tool, never per-drawing data', () => {
    expect(styleProperties('bars-pattern', { mode: 'line', mirrored: true, sourceFrom: '2025-01-01', sourceTo: '2025-01-05' })).toEqual({ mode: 'line', mirrored: true });
    rememberToolDefaults({ toolType: 'bars-pattern', style: DEFAULT_DRAWING_STYLE, properties: { mode: 'line', sourceFrom: '2025-01-01' } });
    expect(toolDefaults('bars-pattern').properties).toEqual({ mode: 'line' });
    const payload = drawingTemplatePayload('Line', { toolType: 'bars-pattern', style: DEFAULT_DRAWING_STYLE, properties: { mode: 'line', sourceTo: 'x' } });
    expect(payload.properties).toEqual({ mode: 'line' });
  });
});

describe('drawing tool settings (TVP-3.8)', () => {
  it('persist in the browser and fall back to the defaults', () => {
    expect(loadDrawingToolSettings()).toEqual(DEFAULT_DRAWING_TOOL_SETTINGS);
    saveDrawingToolSettings({ ...DEFAULT_DRAWING_TOOL_SETTINGS, lockAll: true, syncDrawings: false });
    expect(loadDrawingToolSettings()).toMatchObject({ lockAll: true, syncDrawings: false, favoritesBar: true });
    window.localStorage.setItem('omnix.trading.drawing-tool-settings', '{"lockAll":"yes"}');
    expect(loadDrawingToolSettings().lockAll).toBe(false);
  });

  it('drawings sync between a tab\'s charts unless sync is off', () => {
    expect(drawingScopeId('tab-1', 'chart-2', true)).toBe('tab-1');
    expect(drawingScopeId('tab-1', 'chart-2', false)).toBe('tab-1:chart:chart-2');
    expect(drawingScopeId(undefined, 'chart-2', false)).toBe('global:chart:chart-2');
  });
});
