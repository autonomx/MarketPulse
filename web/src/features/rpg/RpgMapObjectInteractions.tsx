import type { CSSProperties, KeyboardEvent, MouseEvent } from 'react';
import type {
  RpgMapDefinition,
  RpgMapObjectDefinition,
  RpgMapObjectDynamicState,
} from './api/rpgMapClient';
import { rpgMapAssetUrl } from './rpgMapAssets';
import type { RpgMapViewportState } from './rpgMapViewport';

const LAYER_PRIORITY: Record<string, number> = {
  background: 0,
  terrain: 10,
  routes: 20,
  ground_props: 30,
  structures: 40,
  markers: 50,
  labels: 60,
  fog: 70,
  interaction: 80,
};

interface RpgMapObjectLayerProps {
  activeObjectId: string | null;
  discoveredObjectIds: Set<string>;
  objectStates: Map<string, RpgMapObjectDynamicState>;
  objects: RpgMapObjectDefinition[];
  onActiveObjectChange: (objectId: string | null) => void;
  onSelectObject: (objectId: string) => void;
  selectedObjectId: string | null;
  visibleObjectIds: Set<string>;
}

export function RpgMapObjectLayer({
  activeObjectId,
  discoveredObjectIds,
  objectStates,
  objects,
  onActiveObjectChange,
  onSelectObject,
  selectedObjectId,
  visibleObjectIds,
}: RpgMapObjectLayerProps) {
  return (
    <g data-map-layer="structures">
      {[...objects].sort(compareObjects).filter((item) => discoveredObjectIds.has(item.id)).map((item) => (
        <MapObjectShape
          active={activeObjectId === item.id}
          dynamicState={objectStates.get(item.id)}
          item={item}
          key={item.id}
          onActiveObjectChange={onActiveObjectChange}
          onSelectObject={onSelectObject}
          selected={selectedObjectId === item.id}
          visible={visibleObjectIds.has(item.id)}
        />
      ))}
    </g>
  );
}

export function RpgMapObjectTooltip({
  definition,
  dynamicState,
  item,
  viewport,
}: {
  definition: RpgMapDefinition;
  dynamicState?: RpgMapObjectDynamicState;
  item: RpgMapObjectDefinition | null;
  viewport: RpgMapViewportState;
}) {
  if (!item) return null;
  const position = projectMapObjectToPercent(definition, item, viewport);
  const status = dynamicState?.status && dynamicState.status !== 'normal' ? humanize(dynamicState.status) : humanize(item.kind);
  return (
    <div
      className="rpg-map-object-tooltip"
      id={`rpg-map-tooltip-${safeDomId(item.id)}`}
      role="tooltip"
      style={{ '--rpg-map-tooltip-left': `${position.left}%`, '--rpg-map-tooltip-top': `${position.top}%` } as CSSProperties}
    >
      <strong>{item.label || item.location_id || item.id}</strong>
      <span>{dynamicState?.presentation_hint || item.description || humanize(item.kind)}</span>
      <small>{status}</small>
    </div>
  );
}

export function projectMapObjectToPercent(
  definition: RpgMapDefinition,
  item: Pick<RpgMapObjectDefinition, 'x' | 'y'>,
  viewport: RpgMapViewportState,
): { left: number; top: number } {
  const transformedX = viewport.panX + item.x * viewport.zoom;
  const transformedY = viewport.panY + item.y * viewport.zoom;
  return {
    left: clamp(((transformedX - definition.bounds.x) / definition.bounds.width) * 100, 6, 94),
    top: clamp(((transformedY - definition.bounds.y) / definition.bounds.height) * 100, 8, 90),
  };
}

function MapObjectShape({
  active,
  dynamicState,
  item,
  onActiveObjectChange,
  onSelectObject,
  selected,
  visible,
}: {
  active: boolean;
  dynamicState?: RpgMapObjectDynamicState;
  item: RpgMapObjectDefinition;
  onActiveObjectChange: (objectId: string | null) => void;
  onSelectObject: (objectId: string) => void;
  selected: boolean;
  visible: boolean;
}) {
  const spriteWidth = item.sprite?.width ?? 480;
  const spriteHeight = item.sprite?.height ?? 360;
  const spriteUrl = rpgMapAssetUrl(item.sprite?.asset_id);
  const interactive = visible && item.kind !== 'decorative' && Boolean(item.hitbox);
  const tooltipId = `rpg-map-tooltip-${safeDomId(item.id)}`;
  const status = dynamicState?.status ?? 'normal';
  const className = [
    'rpg-map-object',
    `rpg-map-object-${item.kind}`,
    `rpg-map-object-status-${status}`,
    visible ? '' : 'rpg-map-object-obscured',
    active ? 'rpg-map-object-active' : '',
    selected ? 'rpg-map-object-selected' : '',
  ].filter(Boolean).join(' ');
  const select = (event: MouseEvent<SVGGElement> | KeyboardEvent<SVGGElement>) => {
    event.stopPropagation();
    if (interactive) onSelectObject(item.id);
  };
  const onKeyDown = (event: KeyboardEvent<SVGGElement>) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    select(event);
  };

  return (
    <g
      aria-describedby={active ? tooltipId : undefined}
      aria-label={`${item.label || item.location_id || item.id} map object`}
      aria-pressed={interactive ? selected : undefined}
      className={className}
      data-map-object-id={item.id}
      data-map-object-status={status}
      data-map-object-visible={visible ? 'true' : 'false'}
      onBlur={() => onActiveObjectChange(null)}
      onClick={select}
      onFocus={() => interactive && onActiveObjectChange(item.id)}
      onKeyDown={onKeyDown}
      onMouseEnter={() => interactive && onActiveObjectChange(item.id)}
      onMouseLeave={() => onActiveObjectChange(null)}
      role={interactive ? 'button' : undefined}
      tabIndex={interactive ? 0 : -1}
      transform={`translate(${item.x} ${item.y})`}
    >
      {item.footprint ? <polygon className="rpg-map-object-footprint" points={polygonPoints(item.footprint.points)} /> : null}
      <g filter="url(#rpg-map-object-shadow)" pointerEvents="none">
        <g className="rpg-map-object-vector-fallback">
          <rect height={spriteHeight} rx={Math.min(90, spriteWidth * 0.12)} width={spriteWidth} x={-spriteWidth / 2} y={-spriteHeight} />
          <path d={`M${-spriteWidth / 2} ${-spriteHeight} L0 ${-spriteHeight - 170} L${spriteWidth / 2} ${-spriteHeight} Z`} />
        </g>
        {spriteUrl ? (
          <image
            aria-hidden="true"
            className="rpg-map-object-sprite"
            data-map-asset-id={item.sprite?.asset_id}
            height={spriteHeight}
            href={spriteUrl}
            preserveAspectRatio="xMidYMax meet"
            width={spriteWidth}
            x={-spriteWidth / 2}
            y={-spriteHeight}
          />
        ) : null}
        <text y={90}>{item.label || item.location_id || item.id}</text>
      </g>
      {item.hitbox ? <polygon className="rpg-map-object-hitbox" data-map-hitbox={item.id} points={polygonPoints(item.hitbox.points)} /> : null}
    </g>
  );
}

function compareObjects(left: RpgMapObjectDefinition, right: RpgMapObjectDefinition): number {
  return (LAYER_PRIORITY[left.render_order.layer] ?? 100) - (LAYER_PRIORITY[right.render_order.layer] ?? 100)
    || left.render_order.sort_y - right.render_order.sort_y
    || left.render_order.offset - right.render_order.offset
    || left.id.localeCompare(right.id);
}

function polygonPoints(points: [number, number][]): string {
  return points.map(([x, y]) => `${x},${y}`).join(' ');
}

function safeDomId(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]/g, '-');
}

function humanize(value: string): string {
  return value.replaceAll('_', ' ');
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}
