import { describe, expect, it } from 'vitest';
import {
  RPG_MAP_MAX_ZOOM,
  fitRpgMapViewport,
  panRpgMapViewport,
  rpgMapClientPointToLogical,
  rpgMapKeyboardViewport,
  rpgMapScreenDeltaToLogical,
  rpgMapViewportTransform,
  zoomRpgMapViewportAt,
} from './rpgMapViewport';

const bounds = { x: 0, y: 0, width: 1000, height: 600 };

describe('rpgMapViewport', () => {
  it('fits and resets to one deterministic viewport', () => {
    expect(fitRpgMapViewport()).toEqual({ panX: 0, panY: 0, zoom: 1 });
    expect(rpgMapViewportTransform(fitRpgMapViewport())).toBe('matrix(1 0 0 1 0 0)');
  });

  it('zooms around an anchor while clamping to the supported budget', () => {
    const zoomed = zoomRpgMapViewportAt(fitRpgMapViewport(), bounds, 2, 500, 300);

    expect(zoomed).toEqual({ zoom: 2, panX: -500, panY: -300 });
    expect(zoomRpgMapViewportAt(zoomed, bounds, 99).zoom).toBe(RPG_MAP_MAX_ZOOM);
  });

  it('clamps drag pan so the transformed map cannot leave the viewport', () => {
    const zoomed = zoomRpgMapViewportAt(fitRpgMapViewport(), bounds, 2, 500, 300);

    expect(panRpgMapViewport(zoomed, bounds, 5000, 5000)).toEqual({ zoom: 2, panX: 0, panY: 0 });
    expect(panRpgMapViewport(zoomed, bounds, -5000, -5000)).toEqual({ zoom: 2, panX: -1000, panY: -600 });
  });

  it('maps client points and pointer deltas into logical coordinates', () => {
    const rect = { left: 10, top: 20, width: 500, height: 300 };

    expect(rpgMapClientPointToLogical(260, 170, rect, bounds)).toEqual({ x: 500, y: 300 });
    expect(rpgMapScreenDeltaToLogical(50, -30, rect, bounds)).toEqual({ x: 100, y: -60 });
  });

  it('supports keyboard pan, zoom, and reset controls', () => {
    const zoomed = rpgMapKeyboardViewport(fitRpgMapViewport(), bounds, '+');
    expect(zoomed?.zoom).toBeGreaterThan(1);

    const panned = rpgMapKeyboardViewport(zoomed!, bounds, 'ArrowRight');
    expect(panned?.panX).toBeLessThan(zoomed!.panX);

    expect(rpgMapKeyboardViewport(panned!, bounds, 'Home')).toEqual(fitRpgMapViewport());
    expect(rpgMapKeyboardViewport(panned!, bounds, 'Escape')).toBeNull();
  });
});
