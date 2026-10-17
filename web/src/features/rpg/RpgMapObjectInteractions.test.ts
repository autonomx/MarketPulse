import { describe, expect, it } from 'vitest';
import type { RpgMapDefinition, RpgMapObjectDefinition } from './api/rpgMapClient';
import { projectMapObjectToPercent } from './RpgMapObjectInteractions';

const definition = {
  bounds: { x: 0, y: 0, width: 1000, height: 600 },
} as RpgMapDefinition;

describe('projectMapObjectToPercent', () => {
  it('projects transformed logical coordinates into viewport percentages', () => {
    const item = { x: 250, y: 300 } as RpgMapObjectDefinition;

    expect(projectMapObjectToPercent(definition, item, { zoom: 2, panX: -100, panY: -120 })).toEqual({
      left: 40,
      top: 80,
    });
  });

  it('clamps hover cards inside the visible viewport', () => {
    const leftEdge = { x: -1000, y: -1000 } as RpgMapObjectDefinition;
    const rightEdge = { x: 5000, y: 5000 } as RpgMapObjectDefinition;

    expect(projectMapObjectToPercent(definition, leftEdge, { zoom: 1, panX: 0, panY: 0 })).toEqual({ left: 6, top: 8 });
    expect(projectMapObjectToPercent(definition, rightEdge, { zoom: 1, panX: 0, panY: 0 })).toEqual({ left: 94, top: 90 });
  });
});
