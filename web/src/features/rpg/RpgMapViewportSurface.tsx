import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type WheelEvent } from 'react';
import type { RpgMapDefinition, RpgMapOverlay } from './api/rpgMapClient';
import { RpgMapObjectLayer, RpgMapObjectTooltip } from './RpgMapObjectInteractions';
import {
  DEFAULT_RPG_MAP_LAYERS,
  RpgMapEnvironmentLayer,
  RpgMapFogLayer,
  RpgMapLabelLayer,
  RpgMapLayerControls,
  RpgMapMarkerLayer,
  RpgMapRouteLayer,
  type RpgMapLayerVisibility,
} from './RpgMapOverlayLayers';
import { rpgMapAssetUrl } from './rpgMapAssets';
import {
  RPG_MAP_MAX_ZOOM,
  RPG_MAP_MIN_ZOOM,
  RPG_MAP_ZOOM_STEP,
  fitRpgMapViewport,
  panRpgMapViewport,
  rpgMapClientPointToLogical,
  rpgMapKeyboardViewport,
  rpgMapScreenDeltaToLogical,
  rpgMapViewportTransform,
  zoomRpgMapViewportAt,
  type RpgMapViewportState,
} from './rpgMapViewport';
import './RpgMapViewportSurface.css';

const viewportCache = new Map<string, RpgMapViewportState>();
const layerCache = new Map<string, RpgMapLayerVisibility>();

interface PointerPoint {
  x: number;
  y: number;
}

interface PinchStart {
  distance: number;
  midpoint: PointerPoint;
  viewport: RpgMapViewportState;
}

interface RpgMapViewportSurfaceProps {
  activeObjectId: string | null;
  definition: RpgMapDefinition;
  onActiveObjectChange: (objectId: string | null) => void;
  onSelectObject: (objectId: string) => void;
  overlay: RpgMapOverlay;
  selectedObjectId: string | null;
}

export function RpgMapViewportSurface({
  activeObjectId,
  definition,
  onActiveObjectChange,
  onSelectObject,
  overlay,
  selectedObjectId,
}: RpgMapViewportSurfaceProps) {
  const { x, y, width, height } = definition.bounds;
  const [viewport, setViewport] = useState<RpgMapViewportState>(() => viewportCache.get(definition.map_id) ?? fitRpgMapViewport());
  const [layers, setLayers] = useState<RpgMapLayerVisibility>(() => layerCache.get(definition.map_id) ?? { ...DEFAULT_RPG_MAP_LAYERS });
  const [dragging, setDragging] = useState(false);
  const pointersRef = useRef(new Map<number, PointerPoint>());
  const lastPointRef = useRef<PointerPoint | null>(null);
  const pinchRef = useRef<PinchStart | null>(null);
  const discoveredIds = overlay.availability === 'ready'
    ? new Set(overlay.discovered_object_ids)
    : new Set(definition.objects.map((item) => item.id));
  const visibleIds = overlay.availability === 'ready'
    ? new Set(overlay.visible_object_ids)
    : new Set(definition.objects.map((item) => item.id));
  const objectStates = new Map((overlay.object_states ?? []).map((state) => [state.object_id, state]));
  const activeObject = definition.objects.find((item) => item.id === activeObjectId) ?? null;
  const activeDynamicState = activeObject ? objectStates.get(activeObject.id) : undefined;
  const backgroundUrl = rpgMapAssetUrl(definition.background?.asset_id);

  useEffect(() => {
    setViewport(viewportCache.get(definition.map_id) ?? fitRpgMapViewport());
    setLayers(layerCache.get(definition.map_id) ?? { ...DEFAULT_RPG_MAP_LAYERS });
  }, [definition.map_id]);

  useEffect(() => {
    viewportCache.set(definition.map_id, viewport);
  }, [definition.map_id, viewport]);

  useEffect(() => {
    layerCache.set(definition.map_id, layers);
    if (!layers.structures) onActiveObjectChange(null);
  }, [definition.map_id, layers, onActiveObjectChange]);

  const fitMap = () => setViewport(fitRpgMapViewport());
  const zoomBy = (factor: number) => setViewport(
    zoomRpgMapViewportAt(viewport, definition.bounds, viewport.zoom * factor),
  );

  const onWheel = (event: WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    const anchor = rpgMapClientPointToLogical(event.clientX, event.clientY, rect, definition.bounds);
    setViewport(zoomRpgMapViewportAt(
      viewport,
      definition.bounds,
      viewport.zoom * Math.exp(-event.deltaY * 0.0015),
      anchor.x,
      anchor.y,
    ));
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const next = rpgMapKeyboardViewport(viewport, definition.bounds, event.key, event.shiftKey);
    if (!next) return;
    event.preventDefault();
    setViewport(next);
  };

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (isObjectInteractionTarget(event.target)) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    setDragging(true);
    if (pointersRef.current.size === 1) {
      lastPointRef.current = { x: event.clientX, y: event.clientY };
      pinchRef.current = null;
    } else if (pointersRef.current.size === 2) {
      pinchRef.current = createPinchStart(pointersRef.current, viewport);
      lastPointRef.current = null;
    }
  };

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!pointersRef.current.has(event.pointerId)) return;
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const rect = event.currentTarget.getBoundingClientRect();
    if (pointersRef.current.size >= 2) {
      const start = pinchRef.current ?? createPinchStart(pointersRef.current, viewport);
      const current = pinchMetrics(pointersRef.current);
      pinchRef.current = start;
      if (!start || !current || start.distance <= 0) return;
      const anchor = rpgMapClientPointToLogical(start.midpoint.x, start.midpoint.y, rect, definition.bounds);
      const midpointDelta = rpgMapScreenDeltaToLogical(
        current.midpoint.x - start.midpoint.x,
        current.midpoint.y - start.midpoint.y,
        rect,
        definition.bounds,
      );
      const zoomed = zoomRpgMapViewportAt(
        start.viewport,
        definition.bounds,
        start.viewport.zoom * (current.distance / start.distance),
        anchor.x,
        anchor.y,
      );
      setViewport(panRpgMapViewport(zoomed, definition.bounds, midpointDelta.x, midpointDelta.y));
      return;
    }
    const previous = lastPointRef.current;
    if (!previous) {
      lastPointRef.current = { x: event.clientX, y: event.clientY };
      return;
    }
    const delta = rpgMapScreenDeltaToLogical(
      event.clientX - previous.x,
      event.clientY - previous.y,
      rect,
      definition.bounds,
    );
    setViewport(panRpgMapViewport(viewport, definition.bounds, delta.x, delta.y));
    lastPointRef.current = { x: event.clientX, y: event.clientY };
  };

  const finishPointer = (event: PointerEvent<HTMLDivElement>) => {
    pointersRef.current.delete(event.pointerId);
    if (pointersRef.current.size === 1) {
      lastPointRef.current = [...pointersRef.current.values()][0] ?? null;
      pinchRef.current = null;
    } else if (pointersRef.current.size === 0) {
      lastPointRef.current = null;
      pinchRef.current = null;
      setDragging(false);
    }
  };

  return (
    <div className="rpg-map-viewport-shell">
      <div role="group" className="rpg-map-viewport-controls" aria-label="Map viewport controls">
        <button aria-label="Zoom out" disabled={viewport.zoom <= RPG_MAP_MIN_ZOOM} onClick={() => zoomBy(1 / RPG_MAP_ZOOM_STEP)} type="button">−</button>
        <span aria-live="polite">{Math.round(viewport.zoom * 100)}%</span>
        <button aria-label="Zoom in" disabled={viewport.zoom >= RPG_MAP_MAX_ZOOM} onClick={() => zoomBy(RPG_MAP_ZOOM_STEP)} type="button">+</button>
        <button onClick={fitMap} type="button">Fit map</button>
        <button onClick={fitMap} type="button">Reset view</button>
      </div>
      <RpgMapLayerControls layers={layers} onChange={setLayers} />
      <div
        aria-keyshortcuts="ArrowLeft ArrowRight ArrowUp ArrowDown + - Home 0"
        aria-label="Interactive map viewport. Drag to pan, use the mouse wheel or pinch to zoom, and use arrow keys to pan."
        className={`rpg-map-canvas-frame${dragging ? ' rpg-map-canvas-dragging' : ''}`}
        onKeyDown={onKeyDown}
        onPointerCancel={finishPointer}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={finishPointer}
        onWheel={onWheel}
        role="region"
        tabIndex={0}
      >
        <svg className="rpg-map-canvas" role="img" aria-label={`${definition.map_id} interactive map`} viewBox={`${x} ${y} ${width} ${height}`} preserveAspectRatio="xMidYMid meet">
          <defs>
            <pattern id="rpg-map-parchment-grid" width="320" height="320" patternUnits="userSpaceOnUse">
              <rect width="320" height="320" className="rpg-map-parchment-tile" />
              <path d="M0 160H320M160 0V320" className="rpg-map-parchment-line" />
            </pattern>
            <filter id="rpg-map-object-shadow" x="-20%" y="-20%" width="140%" height="150%">
              <feDropShadow dx="0" dy="24" stdDeviation="18" floodOpacity="0.35" />
            </filter>
          </defs>
          <g data-map-viewport="true" transform={rpgMapViewportTransform(viewport)}>
            <rect x={x} y={y} width={width} height={height} fill="url(#rpg-map-parchment-grid)" />
            {backgroundUrl && definition.background ? (
              <image
                aria-hidden="true"
                className="rpg-map-background-asset"
                data-map-asset-id={definition.background.asset_id}
                height={definition.background.destination_bounds.height}
                href={backgroundUrl}
                preserveAspectRatio="none"
                width={definition.background.destination_bounds.width}
                x={definition.background.destination_bounds.x}
                y={definition.background.destination_bounds.y}
              />
            ) : null}
            <RpgMapEnvironmentLayer definition={definition} environment={overlay.environment} />
            {layers.routes ? <RpgMapRouteLayer definition={definition} overlay={overlay} /> : null}
            {layers.structures ? (
              <RpgMapObjectLayer
                activeObjectId={activeObjectId}
                discoveredObjectIds={discoveredIds}
                objectStates={objectStates}
                objects={definition.objects}
                onActiveObjectChange={onActiveObjectChange}
                onSelectObject={onSelectObject}
                selectedObjectId={selectedObjectId}
                visibleObjectIds={visibleIds}
              />
            ) : null}
            {layers.markers ? <RpgMapMarkerLayer markers={overlay.markers} /> : null}
            {layers.labels ? <RpgMapLabelLayer labels={definition.labels} /> : null}
            {layers.fog ? <RpgMapFogLayer polygons={overlay.fog_polygons ?? []} /> : null}
          </g>
        </svg>
        {layers.structures ? (
          <RpgMapObjectTooltip definition={definition} dynamicState={activeDynamicState} item={activeObject} viewport={viewport} />
        ) : null}
      </div>
    </div>
  );
}

function createPinchStart(pointers: Map<number, PointerPoint>, viewport: RpgMapViewportState): PinchStart | null {
  const metrics = pinchMetrics(pointers);
  return metrics ? { ...metrics, viewport } : null;
}

function pinchMetrics(pointers: Map<number, PointerPoint>): Omit<PinchStart, 'viewport'> | null {
  const [first, second] = [...pointers.values()];
  if (!first || !second) return null;
  return {
    distance: Math.hypot(second.x - first.x, second.y - first.y),
    midpoint: { x: (first.x + second.x) / 2, y: (first.y + second.y) / 2 },
  };
}

function isObjectInteractionTarget(target: EventTarget): boolean {
  return target instanceof Element && Boolean(target.closest('[data-map-object-id]'));
}
