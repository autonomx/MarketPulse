import type {
  RpgMapDefinition,
  RpgMapFogPolygon,
  RpgMapLabelDefinition,
  RpgMapMarker,
  RpgMapOverlay,
  RpgMapRouteGeometry,
  RpgMapRouteOverlay,
} from './api/rpgMapClient';

export interface RpgMapLayerVisibility {
  fog: boolean;
  labels: boolean;
  markers: boolean;
  routes: boolean;
  structures: boolean;
}

export const DEFAULT_RPG_MAP_LAYERS: RpgMapLayerVisibility = Object.freeze({
  fog: true,
  labels: true,
  markers: true,
  routes: true,
  structures: true,
});

export function RpgMapLayerControls({
  layers,
  onChange,
}: {
  layers: RpgMapLayerVisibility;
  onChange: (layers: RpgMapLayerVisibility) => void;
}) {
  return (
    <fieldset className="rpg-map-layer-controls">
      <legend>Map layers</legend>
      {(Object.keys(DEFAULT_RPG_MAP_LAYERS) as (keyof RpgMapLayerVisibility)[]).map((layer) => (
        <label key={layer}>
          <input
            checked={layers[layer]}
            onChange={() => onChange({ ...layers, [layer]: !layers[layer] })}
            type="checkbox"
          />
          {humanize(layer)}
        </label>
      ))}
    </fieldset>
  );
}

export function RpgMapEnvironmentLayer({ definition, environment }: {
  definition: RpgMapDefinition;
  environment: Record<string, string>;
}) {
  const weather = environment.weather?.toLowerCase() ?? '';
  const light = environment.light?.toLowerCase() ?? '';
  const visibility = environment.visibility?.toLowerCase() ?? '';
  const classes = [
    'rpg-map-environment',
    weather ? `rpg-map-weather-${safeClass(weather)}` : '',
    light ? `rpg-map-light-${safeClass(light)}` : '',
    visibility ? `rpg-map-visibility-${safeClass(visibility)}` : '',
  ].filter(Boolean).join(' ');
  return (
    <rect
      aria-hidden="true"
      className={classes}
      data-map-environment-light={light || undefined}
      data-map-environment-weather={weather || undefined}
      data-map-layer="environment"
      height={definition.bounds.height}
      width={definition.bounds.width}
      x={definition.bounds.x}
      y={definition.bounds.y}
    />
  );
}

export function RpgMapFogLayer({ polygons }: { polygons: RpgMapFogPolygon[] }) {
  return (
    <g data-map-layer="fog">
      {[...polygons].sort((left, right) => left.id.localeCompare(right.id)).map((polygon) => (
        <polygon
          aria-hidden="true"
          className="rpg-map-fog-polygon"
          data-map-fog-id={polygon.id}
          key={polygon.id}
          points={polygon.points.map(([x, y]) => `${x},${y}`).join(' ')}
        />
      ))}
    </g>
  );
}

export function RpgMapRouteLayer({ definition, overlay }: { definition: RpgMapDefinition; overlay: RpgMapOverlay }) {
  const routeStates = new Map(overlay.routes.map((route) => [route.route_id, route]));
  return (
    <g data-map-layer="routes">
      {definition.route_geometry.map((geometry) => (
        <RoutePath geometry={geometry} key={geometry.route_id} state={routeStates.get(geometry.route_id)} />
      ))}
    </g>
  );
}

export function RpgMapLabelLayer({ labels }: { labels: RpgMapLabelDefinition[] }) {
  return (
    <g data-map-layer="labels">
      {[...labels].sort((left, right) => right.priority - left.priority || left.id.localeCompare(right.id)).map((label) => (
        <text className="rpg-map-label" data-map-label-id={label.id} key={label.id} x={label.x} y={label.y}>
          {label.text}
        </text>
      ))}
    </g>
  );
}

export function RpgMapMarkerLayer({ markers }: { markers: RpgMapMarker[] }) {
  return (
    <g data-map-layer="markers">
      {[...markers].sort((left, right) => left.id.localeCompare(right.id)).map((marker) => (
        <MapMarker key={marker.id} marker={marker} />
      ))}
    </g>
  );
}

function RoutePath({ geometry, state }: { geometry: RpgMapRouteGeometry; state?: RpgMapRouteOverlay }) {
  if (state && !state.known) return null;
  const status = state?.status ?? 'unknown';
  const safety = state?.safe === false ? 'dangerous' : 'safe';
  return (
    <polyline
      aria-label={`${geometry.route_id} ${status} route`}
      className={`rpg-map-route rpg-map-route-${status} rpg-map-route-${safety} rpg-map-route-style-${geometry.style}`}
      data-map-route-id={geometry.route_id}
      fill="none"
      points={geometry.points.map(([x, y]) => `${x},${y}`).join(' ')}
      role="img"
    />
  );
}

function MapMarker({ marker }: { marker: RpgMapMarker }) {
  const label = marker.label || humanize(marker.kind);
  return (
    <g
      aria-label={`${label} ${marker.kind} marker`}
      className={`rpg-map-marker rpg-map-marker-${marker.kind}`}
      data-map-marker-id={marker.id}
      data-map-marker-kind={marker.kind}
      role="img"
      transform={`translate(${marker.x} ${marker.y})`}
    >
      {marker.kind === 'player' ? (
        <>
          <circle r="92" />
          <path d="M0-120 72 18 0 86-72 18Z" />
        </>
      ) : marker.kind === 'danger' ? (
        <path d="M0-105 100 82-100 82Z" />
      ) : marker.kind === 'quest' ? (
        <path d="M0-105 86 0 0 105-86 0Z" />
      ) : marker.kind === 'event' ? (
        <path d="M0-100 28-32 100 0 28 32 0 100-28 32-100 0-28-32Z" />
      ) : (
        <circle r="74" />
      )}
      <text y={150}>{label}</text>
    </g>
  );
}

function humanize(value: string): string {
  return value.replaceAll('_', ' ');
}

function safeClass(value: string): string {
  return value.replace(/[^a-z0-9_-]/g, '-');
}
