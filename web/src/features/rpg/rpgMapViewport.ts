export interface RpgMapLogicalBounds {
  height: number;
  width: number;
  x: number;
  y: number;
}

export interface RpgMapViewportState {
  panX: number;
  panY: number;
  zoom: number;
}

export const RPG_MAP_MIN_ZOOM = 1;
export const RPG_MAP_MAX_ZOOM = 4;
export const RPG_MAP_ZOOM_STEP = 1.25;

export const FIT_RPG_MAP_VIEWPORT: RpgMapViewportState = Object.freeze({
  panX: 0,
  panY: 0,
  zoom: 1,
});

export function fitRpgMapViewport(): RpgMapViewportState {
  return { ...FIT_RPG_MAP_VIEWPORT };
}

export function clampRpgMapViewport(
  viewport: RpgMapViewportState,
  bounds: RpgMapLogicalBounds,
): RpgMapViewportState {
  const zoom = clamp(viewport.zoom, RPG_MAP_MIN_ZOOM, RPG_MAP_MAX_ZOOM);
  const minPanX = bounds.x + bounds.width - zoom * (bounds.x + bounds.width);
  const maxPanX = bounds.x - zoom * bounds.x;
  const minPanY = bounds.y + bounds.height - zoom * (bounds.y + bounds.height);
  const maxPanY = bounds.y - zoom * bounds.y;
  return {
    zoom,
    panX: clamp(viewport.panX, Math.min(minPanX, maxPanX), Math.max(minPanX, maxPanX)),
    panY: clamp(viewport.panY, Math.min(minPanY, maxPanY), Math.max(minPanY, maxPanY)),
  };
}

export function panRpgMapViewport(
  viewport: RpgMapViewportState,
  bounds: RpgMapLogicalBounds,
  deltaX: number,
  deltaY: number,
): RpgMapViewportState {
  return clampRpgMapViewport(
    { ...viewport, panX: viewport.panX + deltaX, panY: viewport.panY + deltaY },
    bounds,
  );
}

export function zoomRpgMapViewportAt(
  viewport: RpgMapViewportState,
  bounds: RpgMapLogicalBounds,
  requestedZoom: number,
  anchorX: number = bounds.x + bounds.width / 2,
  anchorY: number = bounds.y + bounds.height / 2,
): RpgMapViewportState {
  const nextZoom = clamp(requestedZoom, RPG_MAP_MIN_ZOOM, RPG_MAP_MAX_ZOOM);
  if (nextZoom === viewport.zoom) return clampRpgMapViewport(viewport, bounds);
  const ratio = nextZoom / viewport.zoom;
  return clampRpgMapViewport(
    {
      zoom: nextZoom,
      panX: anchorX - (anchorX - viewport.panX) * ratio,
      panY: anchorY - (anchorY - viewport.panY) * ratio,
    },
    bounds,
  );
}

export function rpgMapClientPointToLogical(
  clientX: number,
  clientY: number,
  rect: Pick<DOMRect, 'height' | 'left' | 'top' | 'width'>,
  bounds: RpgMapLogicalBounds,
): { x: number; y: number } {
  const normalizedX = rect.width > 0 ? (clientX - rect.left) / rect.width : 0.5;
  const normalizedY = rect.height > 0 ? (clientY - rect.top) / rect.height : 0.5;
  return {
    x: bounds.x + clamp(normalizedX, 0, 1) * bounds.width,
    y: bounds.y + clamp(normalizedY, 0, 1) * bounds.height,
  };
}

export function rpgMapScreenDeltaToLogical(
  deltaX: number,
  deltaY: number,
  rect: Pick<DOMRect, 'height' | 'width'>,
  bounds: RpgMapLogicalBounds,
): { x: number; y: number } {
  return {
    x: rect.width > 0 ? (deltaX / rect.width) * bounds.width : 0,
    y: rect.height > 0 ? (deltaY / rect.height) * bounds.height : 0,
  };
}

export function rpgMapViewportTransform(viewport: RpgMapViewportState): string {
  return `matrix(${viewport.zoom} 0 0 ${viewport.zoom} ${viewport.panX} ${viewport.panY})`;
}

export function rpgMapKeyboardViewport(
  viewport: RpgMapViewportState,
  bounds: RpgMapLogicalBounds,
  key: string,
  shiftKey = false,
): RpgMapViewportState | null {
  const panStep = Math.max(bounds.width, bounds.height) * (shiftKey ? 0.12 : 0.045) / viewport.zoom;
  if (key === 'ArrowLeft') return panRpgMapViewport(viewport, bounds, panStep, 0);
  if (key === 'ArrowRight') return panRpgMapViewport(viewport, bounds, -panStep, 0);
  if (key === 'ArrowUp') return panRpgMapViewport(viewport, bounds, 0, panStep);
  if (key === 'ArrowDown') return panRpgMapViewport(viewport, bounds, 0, -panStep);
  if (key === '+' || key === '=') return zoomRpgMapViewportAt(viewport, bounds, viewport.zoom * RPG_MAP_ZOOM_STEP);
  if (key === '-' || key === '_') return zoomRpgMapViewportAt(viewport, bounds, viewport.zoom / RPG_MAP_ZOOM_STEP);
  if (key === '0' || key === 'Home') return fitRpgMapViewport();
  return null;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, Number.isFinite(value) ? value : minimum));
}
