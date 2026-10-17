// Defensive reads of tool properties. Stored properties are kept as they are
// (an older or newer client may have written another shape), so geometry reads
// them through these helpers and falls back when a value doesn't fit.
import type { DrawingProperties, DrawingPropertyField, DrawingPropertyRecord } from './types';

export function booleanProperty(properties: DrawingProperties, key: string, fallback: boolean): boolean {
  const value = properties[key];
  return typeof value === 'boolean' ? value : fallback;
}

export function numberProperty(properties: DrawingProperties, key: string, fallback: number): number {
  const value = properties[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

export function stringProperty(properties: DrawingProperties, key: string, fallback: string): string {
  const value = properties[key];
  return typeof value === 'string' ? value : fallback;
}

export function numberListProperty(properties: DrawingProperties, key: string, fallback: readonly number[]): readonly number[] {
  const value = properties[key];
  return Array.isArray(value) && value.every((item) => typeof item === 'number' && Number.isFinite(item)) ? value as readonly number[] : fallback;
}

export function recordsProperty(properties: DrawingProperties, key: string, fallback: readonly DrawingPropertyRecord[]): readonly DrawingPropertyRecord[] {
  const value = properties[key];
  return Array.isArray(value) && value.every((item) => typeof item === 'object' && item !== null && !Array.isArray(item))
    ? value as readonly DrawingPropertyRecord[]
    : fallback;
}

/** Whether a stored value has the shape its schema field expects (the settings dialog edits only those). */
export function propertyValueReadable(field: DrawingPropertyField, value: unknown): boolean {
  switch (field.type) {
    case 'boolean':
      return typeof value === 'boolean';
    case 'number':
      return typeof value === 'number' && Number.isFinite(value);
    case 'number-list':
      return Array.isArray(value) && value.every((item) => typeof item === 'number' && Number.isFinite(item));
    case 'select':
      return typeof value === 'string' && field.options.some((option) => option.value === value);
    case 'color':
    case 'text':
      return typeof value === 'string';
    case 'records':
      return Array.isArray(value) && value.every((item) => typeof item === 'object' && item !== null && !Array.isArray(item));
  }
}

/** A number kept within a field's min and max. */
export function clampToField(value: number, field: { min?: number; max?: number }): number {
  return Math.min(field.max ?? Number.POSITIVE_INFINITY, Math.max(field.min ?? Number.NEGATIVE_INFINITY, value));
}
