// Drawing templates (TVP-3.8).
//
// - Each tool remembers the last style and properties set on one of its
//   drawings (as TradingView does): new drawings of that tool start with them.
//   Kept in this browser, like the toolbar favourites.
// - Named templates per tool ("Save drawing template as...", then apply it to
//   any drawing of that tool) are saved with the indicator-preset documents,
//   marked `drawing-template`, so they follow the account like chart templates.
import type { TradingDocument } from '../tradingTypes';
import { drawingToolDefinition } from './tools/registry';
import { DEFAULT_DRAWING_STYLE, type DrawingProperties, type DrawingStyle, type TradingDrawing } from './drawingCommands';

export const DRAWING_TEMPLATE_KIND = 'drawing-template';
const DEFAULTS_KEY = 'omnix.trading.drawing-tool-defaults';

export type DrawingToolDefaults = { style?: DrawingStyle; properties?: DrawingProperties };
export type DrawingTemplate = { recordId: string; name: string; toolType: string; style: DrawingStyle; properties: DrawingProperties };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** A stored style, or null when it isn't one. */
export function parseStyle(value: unknown): DrawingStyle | null {
  if (!isRecord(value)) return null;
  const { color, lineWidth, lineStyle } = value;
  if (typeof color !== 'string' || typeof lineWidth !== 'number' || !Number.isFinite(lineWidth) || (lineStyle !== 'solid' && lineStyle !== 'dashed')) return null;
  return { color, lineWidth, lineStyle };
}

/**
 * The properties a template or a tool default carries: only the ones the tool's settings edit. Per-drawing data
 * such as a bars pattern's source range never moves to another drawing.
 */
export function styleProperties(toolType: string, properties: DrawingProperties | undefined): DrawingProperties {
  const keys = new Set(drawingToolDefinition(toolType)?.propertySchema.map((field) => field.key) ?? []);
  return Object.fromEntries(Object.entries(properties ?? {}).filter(([key]) => keys.has(key)));
}

function readDefaults(): Record<string, DrawingToolDefaults> {
  try {
    const stored = JSON.parse(window.localStorage.getItem(DEFAULTS_KEY) ?? '{}') as unknown;
    return isRecord(stored) ? (stored as Record<string, DrawingToolDefaults>) : {};
  } catch {
    return {};
  }
}

/** The style and properties a new drawing of `toolType` starts with (the last ones set on that tool). */
export function toolDefaults(toolType: string): { style: DrawingStyle; properties: DrawingProperties } {
  const remembered = readDefaults()[toolType];
  return {
    style: parseStyle(remembered?.style) ?? DEFAULT_DRAWING_STYLE,
    properties: isRecord(remembered?.properties) ? styleProperties(toolType, remembered.properties as DrawingProperties) : {},
  };
}

/** Remembers a drawing's style and properties for its tool's next drawings. */
export function rememberToolDefaults(drawing: Pick<TradingDrawing, 'toolType' | 'style' | 'properties'>): void {
  try {
    const all = readDefaults();
    all[drawing.toolType] = { style: drawing.style ?? DEFAULT_DRAWING_STYLE, properties: styleProperties(drawing.toolType, drawing.properties) };
    window.localStorage.setItem(DEFAULTS_KEY, JSON.stringify(all));
  } catch {
    // Without storage, new drawings keep the built-in style.
  }
}

/** Forgets what a tool remembered: its next drawings use the built-in style. */
export function resetToolDefaults(toolType: string): void {
  try {
    const all = readDefaults();
    delete all[toolType];
    window.localStorage.setItem(DEFAULTS_KEY, JSON.stringify(all));
  } catch {
    // Nothing remembered without storage.
  }
}

export function drawingTemplatePayload(name: string, drawing: Pick<TradingDrawing, 'toolType' | 'style' | 'properties'>): Record<string, unknown> {
  return {
    name: name.trim() || 'Drawing template',
    templateKind: DRAWING_TEMPLATE_KIND,
    templateVersion: 1,
    toolType: drawing.toolType,
    style: { ...(drawing.style ?? DEFAULT_DRAWING_STYLE) },
    properties: styleProperties(drawing.toolType, drawing.properties),
  };
}

export function parseDrawingTemplate(record: Pick<TradingDocument, 'record_id' | 'payload' | 'status'>): DrawingTemplate | null {
  if (record.status !== 'active' || !isRecord(record.payload)) return null;
  const payload = record.payload;
  if (payload.templateKind !== DRAWING_TEMPLATE_KIND || typeof payload.toolType !== 'string') return null;
  const style = parseStyle(payload.style);
  if (!style || !isRecord(payload.properties)) return null;
  return {
    recordId: record.record_id,
    name: typeof payload.name === 'string' && payload.name.trim() ? payload.name.trim() : record.record_id,
    toolType: payload.toolType,
    style,
    properties: styleProperties(payload.toolType, payload.properties as DrawingProperties),
  };
}

export function drawingTemplateRecordId(name: string, now = Date.now()): string {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 48) || 'drawing';
  return `drawing-template-${slug}-${now}`;
}
